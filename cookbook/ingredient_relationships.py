"""Small, explicit culinary identity relationships, not dietary certification.

Children are narrower foods/cuts, not aliases: searching oxtail must not match
every beef recipe. Derived products stay separate and are opt-in group members.
Ambiguous 'fillet', 'mince', 'stock', 'short ribs' and product composition are not
assigned to an animal without source evidence. Keep this table reviewable.
"""
VERSION = 'ingredient-relationships-11'
CHILDREN = {
    'beef': ('oxtail', 'ox cheek', 'ox tongue', 'ribeye', 'rib eye', 'rib eye steak', 'sirloin steak',
             'rump steak', 'flank steak', 'skirt steak', 'porterhouse steak',
             't bone steak', 'chuck steak', 'chuck roast', 'beef short rib'),
    'pork': ('bacon', 'pancetta', 'guanciale', 'prosciutto', 'pork belly', 'pork shoulder'),
    'lamb': ('hogget', 'mutton'),
    'fish': ('salmon', 'trout', 'tuna', 'cod', 'halibut', 'haddock', 'sea bass',
             'snapper', 'sardine', 'mackerel', 'anchovy', 'arctic char', 'tilapia',
             'monkfish', 'hake', 'turbot', 'bream', 'sole', 'eel', 'whitefish', 'white fish',
             'swordfish', 'mullet', 'trevally', 'kingfish', 'kahawai', 'flounder',
             'grouper', 'herring', 'gurnard', 'whiting', 'john dory', 'sprat',
             'whitebait', 'mahi mahi', 'perch', 'barramundi', 'shark', 'tope', 'tench', 'carp', 'pike', 'skate', 'sturgeon', 'catfish'),
}
# Observed cookbook ingredient names. Keep these separate from finfish and do not
# guess ambiguous stems (for example "pipi russu" is a pepper, not a clam).
SEAFOOD_ADDITIONS = ('shellfish', 'cockle', 'abalone', 'langoustine', 'sea urchin', 'kina')
# These compounds do not imply the animal ingredient; the source may still
# contain it elsewhere. Applied only to named animal/seafood identities.
NON_ANIMAL_COMPOUNDS = ('oyster mushroom', 'king oyster mushroom', 'oyster cracker', 'fish mint', 'scallop squash',
    "lamb's lettuce", 'lamb’s lettuce', 'lambs lettuce', "lamb's quarter", 'lamb’s quarter',
    'chicken of the woods', 'chicken fried steak', 'chicken fried mushroom',
    'vegetarian bacon', 'vegan bacon',
    'vegetarian fish sauce', 'vegan fish sauce', 'vegetarian oyster sauce', 'vegan oyster sauce')
# Source explicitly identifies this clam name as shell color, not chocolate.
# Mask only for the ambiguous ingredient, preserving the seafood identity.
NON_INGREDIENT_HOMONYMS = {
    'chocolate': ('chocolate clam',),
    'beef': ('beef tomato', 'beefsteak tomato'),
}
# A source can elide the repeated noun: "mixed mushrooms, such as cremini
# (chestnut) and oyster". Restrict this mask to an explicit example list of
# known mushroom varieties; never suppress an oyster elsewhere in the row.
_MUSHROOM_VARIETY = r'(?:cremini|chestnut|button|shiitake|portobello|portabella|porcini|enoki|morel|chanterelle)s?'
MUSHROOM_EXAMPLE_PATTERN = (
    r'\bmushrooms?\s*,?\s+such as\s+'
    r'(?:' + _MUSHROOM_VARIETY + r'(?:\s*\(' + _MUSHROOM_VARIETY + r'\))?'
    r'\s*(?:,\s*(?:(?:and|or)\s+)?|(?:and|or)\s+))*'
    r'(?:king\s+)?oysters?\b(?!\s+sauce\b)'
)
PORK_ALTERNATIVES = ('turkey bacon', 'duck bacon', 'beef bacon',
                     *(f'{fish} bacon' for fish in CHILDREN['fish']))
ANIMAL_TERMS = set(CHILDREN) | {t for terms in CHILDREN.values() for t in terms} | {
    'chicken','seafood','shrimp','prawn','crab','lobster','crayfish','mussel','clam',
    'oyster','scallop','squid','octopus','cuttlefish','fish sauce','oyster sauce',
    'shrimp paste','prawn paste','bonito','katsuobushi','bottarga','fish roe','caviar', *SEAFOOD_ADDITIONS}
DERIVATIVE_SUFFIXES = ('stock','broth','bouillon','fat','dripping','tallow')


def related_terms(term):
    return (term, *CHILDREN.get(term, ()))


def derivative_terms(term):
    if term not in set(CHILDREN) | {'chicken'}: return ()
    return tuple(f'{child} {suffix}' for child in related_terms(term) for suffix in DERIVATIVE_SUFFIXES)


BEEF_TERMS = {'beef', *CHILDREN['beef'], *derivative_terms('beef')}
PORK_TERMS = {'pork', *CHILDREN['pork'], *derivative_terms('pork')}
for _animal in (*CHILDREN, 'chicken'):
    ANIMAL_TERMS.update(derivative_terms(_animal))
