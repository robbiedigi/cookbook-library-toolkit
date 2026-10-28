import unittest
from cookbook.pdf_batch_review import quantity, unit_warnings, page_view, partition

class BatchReviewTests(unittest.TestCase):
    def test_mixed_and_fraction(self):
        self.assertEqual(quantity('1½'),1.5)
        self.assertEqual(quantity('1 1/2'),1.5)
    def test_mass_conflict(self):
        self.assertEqual(len(unit_warnings('1 pound (304 g)')),1)
        self.assertEqual(unit_warnings('1 pound (455 g)'),[])
    def test_density_not_conversion(self):
        self.assertEqual(unit_warnings('1 cup (125 g) flour; 1 cup (250 g) water'),[])
    def test_small_rounding(self):
        self.assertEqual(unit_warnings('⅔ cup (165 ml) cream'),[])
        self.assertEqual(len(unit_warnings('½ cup (240 ml) cream')),1)
    def test_header_filter_and_accounting(self):
        blocks=[dict(id=str(i),kind=k,text=t,pdf_pages=[2]) for i,(k,t) in enumerate([('section_header','MY SOUP'),('section_header','SERVES 4'),('text','1 cup water'),('picture',''),('text','OceanofPDF.com')])]
        sections,excluded=partition(blocks,dict(body=[1,3],book_id='cook-beautiful'))
        self.assertEqual(len(sections),1);self.assertEqual(len(excluded),2)
        self.assertFalse(sections[0]['verified_complete_recipe'])
    def test_compound_mass(self):
        self.assertEqual(unit_warnings('1 lb 2 oz/500 g'),[])
        self.assertEqual(unit_warnings('1 lb/2 oz/500 g'),[])
        self.assertEqual(len(unit_warnings('1 lb 5 oz/600 kg')),1)
    def test_correction_is_separate_and_anchored(self):
        from cookbook.pdf_batch_review import corrected_page_views
        evidence=[dict(pdf_page=2,lines=[dict(cell_index=1,text='⅓ cup oil')])]
        ledger=dict(source_sha256='a',edits=[dict(pdf_page=2,cell_index=1,before='⅓ cup oil',after='⅔ cup oil')])
        fixed=corrected_page_views(dict(sha256='a'),evidence,ledger)
        self.assertEqual(fixed[0]['lines'][0]['text'],'⅓ cup oil')
        self.assertEqual(fixed[0]['reading_text'],'⅔ cup oil')
        self.assertNotIn('reading_text',evidence[0])
        with self.assertRaises(ValueError):corrected_page_views(dict(sha256='b'),evidence,ledger)
        stale=dict(ledger,edits=[dict(ledger['edits'][0],before='different')])
        with self.assertRaises(ValueError):corrected_page_views(dict(sha256='a'),evidence,stale)
    def test_geometry_keeps_continuations_and_every_cell(self):
        from cookbook.pdf_batch_review import geometric_sections
        def cell(text,x,y):
            return dict(text=text,rect=dict(coord_origin='TOPLEFT',r_x0=x,r_x1=x+20,r_x2=x+20,r_x3=x,r_y0=y,r_y1=y,r_y2=y+2,r_y3=y+2))
        pages={n:dict(pdf_page=n,size=dict(width=100,height=100),cells=cells) for n,cells in [(1,[cell('Front matter',5,10)]),(2,[cell('REGION: TEST',45,20),cell('SOUP',5,22),cell('1 cup water',5,30),cell('Simmer',45,30)]),(3,[cell('Serve warm',45,10),cell('REGION: TEST',45,50),cell('SALAD',5,52),cell('Mix greens',45,65)])]}
        blocks=[dict(kind='section_header',text=t,provenance=[dict(page_no=n,bbox=dict(coord_origin='TOPLEFT',l=5,r=25,t=y,b=y+2))]) for n,t,y in [(2,'SOUP',22),(3,'SALAD',52)]]
        sections,excluded=geometric_sections(dict(sha256='test'),pages,blocks,dict(book_id='mexico',body=[2,3]))
        self.assertEqual([s['title'] for s in sections],['SOUP','SALAD'])
        self.assertIn('Serve warm',sections[0]['source_text'])
        self.assertEqual(sum(len(s['source_lines']) for s in sections)+len(excluded),9)
        self.assertTrue(all(not s['verified_complete_recipe'] for s in sections))
