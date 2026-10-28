import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed

from .importer import load_recipes
from .llm import MODEL, ask_json

COURSES = ["main", "salad", "side", "dessert", "soup", "snack", "bread", "condiment", "other"]
PROFILE_SCHEMA = {"type": "object", "properties": {"profiles": {"type": "array", "items": {
    "type": "object", "properties": {
        "id": {"type": "string"}, "course": {"type": "string", "enum": COURSES},
        "description": {"type": "string"}, "flavors": {"type": "array", "items": {"type": "string"}},
        "textures": {"type": "array", "items": {"type": "string"}},
        "techniques": {"type": "array", "items": {"type": "string"}},
        "cuisine_associations": {"type": "array", "items": {"type": "string"}},
        "pairing_role": {"type": "string"}, "richness": {"type": "string", "enum": ["light", "moderate", "rich", "unknown"]}},
    "required": ["id", "course", "description", "flavors", "textures", "techniques", "cuisine_associations", "pairing_role", "richness"]}}}, "required": ["profiles"]}
SYSTEM = """Describe recipes for culinary search. Return exactly one profile per supplied recipe ID.
All fields are interpretations, not new source facts. Ground them in the supplied main recipe, not optional variations or suggested accompaniments.
Description: at most 65 words about the actual dish, central ingredients and preparation. Preserve distinctions like fennel bulb vs fennel seed.
Use short culinary phrases for flavors, textures, techniques, cuisine associations and pairing_role. Avoid generic praise.
Richness describes perceived richness, never calorie content. Cuisine associations may be inferred but do not claim historical origin.
Do not infer allergy safety, exact cooking times, nutrition or quantities. A savory salad is a salad; a sweet fruit preparation served after dinner is dessert.
Pairing_role: one sentence about contrast or continuity it can offer, without asserting a uniquely correct pairing.
"""


def fingerprint(recipe):
    return hashlib.sha256(recipe["source_text"].encode()).hexdigest()


def load_profiles(data_dir):
    path = data_dir / "profiles.jsonl"
    return {x["id"]: x for x in map(json.loads, path.read_text().splitlines())} if path.exists() else {}


def enrich(data_dir, limit=None, workers=4):
    recipes = load_recipes(data_dir)
    profiles = load_profiles(data_dir)
    todo = [r for r in recipes if profiles.get(r["id"], {}).get("source_hash") != fingerprint(r)]
    if limit: todo = todo[:limit]
    batches = [todo[i:i + 4] for i in range(0, len(todo), 4)]
    def run(batch):
        payload = [{"id": r["id"], "title": r["title"], "ingredients": r["ingredients"],
                    "headnote": r["headnote"][:6000], "instructions": r["instructions"]} for r in batch]
        result, usage = ask_json(SYSTEM, payload, PROFILE_SCHEMA, max_tokens=3500)
        expected = {r["id"]: r for r in batch}
        if len(result["profiles"]) != len(batch) or {p["id"] for p in result["profiles"]} != set(expected):
            raise ValueError("Profile IDs did not match input recipes.")
        for p in result["profiles"]:
            if p.get("course") not in COURSES: raise ValueError("Invalid course.")
            p.update(source_hash=fingerprint(expected[p["id"]]), model=MODEL, generated=True)
        return result["profiles"], usage
    completed, errors, usage_total = 0, [], {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(run, b): b for b in batches}
        for future in as_completed(futures):
            try:
                items, usage = future.result()
                for p in items: profiles[p["id"]] = p
                for k, value in usage.items():
                    if isinstance(value, int): usage_total[k] = usage_total.get(k, 0) + value
                # Single writer, atomic replacement; completed work survives interruption.
                temp = data_dir / "profiles.jsonl.tmp"
                temp.write_text("".join(json.dumps(p, ensure_ascii=False) + "\n" for p in profiles.values()))
                temp.replace(data_dir / "profiles.jsonl")
                completed += len(items)
                print(f"Profiles: {completed}/{len(todo)}", flush=True)
            except Exception as exc:
                errors.append({"ids": [r["id"] for r in futures[future]], "error": str(exc)})
                print(f"Profile batch failed: {exc}", flush=True)
    report = {"completed": completed, "cached_profiles": len(profiles), "errors": errors, "usage": usage_total, "model": MODEL}
    (data_dir / "enrichment_report.json").write_text(json.dumps(report, indent=2))
    return report
