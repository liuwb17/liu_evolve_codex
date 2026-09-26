import unittest
from aad.v3_diagnostics import layout_diagnostics


class LayoutDiagnosticTests(unittest.TestCase):
    def test_corners_do_not_block_and_board_is_reported(self):
        data='3\n2 2 100\n5 1 8\n4 4 1\n'
        output='0 0 4 4\n5 1 9 3\n4 4 5 5\n'
        result=layout_diagnostics(data,output)
        worst=result['worst_rectangles'][0]
        self.assertEqual(worst['index'],0)
        self.assertEqual(worst['fixed_layout_edge_constraints']['x_max']['free_expansion_distance'],1)
        self.assertEqual(worst['fixed_layout_edge_constraints']['x_max']['nearest_rectangles'][0]['index'],1)
        self.assertEqual(worst['fixed_layout_edge_constraints']['y_max']['free_expansion_distance'],9996)
        self.assertTrue(worst['fixed_layout_edge_constraints']['x_min']['board_is_limit'])
        self.assertEqual(result['unused_board_area'],100000000-25)

    def test_all_tied_blockers_are_counted(self):
        result=layout_diagnostics('3\n2 2 100\n5 0 2\n5 2 2\n',
                                  '0 0 4 4\n5 0 6 2\n5 2 6 4\n')
        edge=result['worst_rectangles'][0]['fixed_layout_edge_constraints']['x_max']
        self.assertEqual(edge['free_expansion_distance'],1)
        self.assertEqual(edge['blocker_count'],2)
        self.assertEqual([r['index'] for r in edge['nearest_rectangles']],[1,2])

    def test_missing_anchor_is_zero_and_illegal_layout_rejected(self):
        result=layout_diagnostics('1\n9 9 16\n','0 0 4 4\n')
        self.assertFalse(result['worst_rectangles'][0]['contains_anchor'])
        self.assertEqual(result['worst_rectangles'][0]['satisfaction'],0)
        with self.assertRaises(ValueError):
            layout_diagnostics('2\n0 0 1\n1 1 1\n','0 0 2 2\n1 1 3 3\n')


if __name__=='__main__':unittest.main()
