import hashlib
import json
import sqlite3
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from cookbook.search import build_index


class VectorReuseTests(unittest.TestCase):
    def test_changed_inputs_reembed_and_result_matches_full_build(self):
        # Keep connections alive to expose leaks even on platforms that allow
        # deleting open database files. SQLite's transaction context does not close.
        connect = sqlite3.connect
        connections = []
        class TrackedConnection(sqlite3.Connection):
            closed = False
            def close(self):
                self.closed = True
                super().close()
        def tracked_connect(*args, **kwargs):
            db = connect(*args, factory=TrackedConnection, **kwargs)
            connections.append(db)
            return db
        connection_patch = patch('cookbook.search.sqlite3.connect', side_effect=tracked_connect)
        connection_patch.start()
        self.addCleanup(connection_patch.stop)
        class Model:
            seen=[]
            def passage_embed(self,texts,batch_size):
                self.seen.extend(texts)
                for text in texts:
                    yield np.array(list(hashlib.sha256(text.encode()).digest())[:8],dtype=np.float32)
        def recipe(id,title):
            return dict(id=id,title=title,ingredients=['1 apple'],headnote='Fresh.',book='Book',book_id='book',instructions=['Chop.'],source_text='Fresh.',source=dict(member='chapter.xhtml'),blocks=[])
        def write(root,records):
            root.mkdir(exist_ok=True);(root/'recipes.jsonl').write_text(''.join(json.dumps(r)+'\n'for r in records))
        with tempfile.TemporaryDirectory()as folder:
            base=Path(folder)/'base';inc=Path(folder)/'inc';full=Path(folder)/'full';model=Model()
            original=[recipe('a','Apple salad'),recipe('b','Apple soup')]
            write(base,original)
            with patch('cookbook.search.embedder',return_value=model):build_index(base)
            revised=[original[0],recipe('b','Roasted apple soup'),recipe('c','Apple tart')]
            write(inc,revised);write(full,revised);model.seen=[]
            with patch('cookbook.search.embedder',return_value=model):report=build_index(inc,reuse_from=base)
            self.assertEqual((report['reused_vectors'],report['embedded_vectors']),(2,4))
            self.assertEqual(len(model.seen),4)
            with patch('cookbook.search.embedder',return_value=model):build_index(full)
            with np.load(inc/'vectors.npz')as a,np.load(full/'vectors.npz')as b:
                for key in ('ids','kinds','vectors'):np.testing.assert_array_equal(a[key],b[key])
            (base/'recipes.jsonl').write_text('changed')
            with patch('cookbook.search.embedder',return_value=model),self.assertRaisesRegex(ValueError,'source/model mismatch'):
                build_index(inc,reuse_from=base)

        self.assertTrue(connections)
        self.assertTrue(all(db.closed for db in connections), "Search index connections leaked")
