import unittest
from types import SimpleNamespace
from unittest.mock import Mock
import numpy as np
from natro_linux.vision import Vision
from natro_linux.memory_match import MemoryMatch, card_content, cleared_card
from natro_linux.engine import DetectionError


class BoardRecognitionTests(unittest.TestCase):
    def test_title_with_different_font_shading(self):
        v = Vision()
        f = np.full((700, 1000, 4), [25, 35, 45, 255], np.uint8)
        f[140:558, 308:692, :3] = [235, 200, 76]
        template = v.templates.get('memorymatch/mmtitle')
        ink = template[:, :, :3].max(2) < 100
        patch = f[155:160, 350:650]
        patch[ink, :3] = 70
        e = SimpleNamespace(vision=v)
        m = MemoryMatch(e, 'Normal')
        self.assertTrue(m.title(f))
        f[530:550, 340:660, :3] = 25
        self.assertFalse(m.title(f))

    def test_closed_hover_and_empty_cards_are_not_items(self):
        for color in ([177,128,62,255], [159,115,56,255], [235,200,76,255]):
            patch = np.full((50,50,4),color,np.uint8)
            self.assertFalse(card_content(patch))
        self.assertTrue(cleared_card(patch))
        self.assertFalse(cleared_card(np.full((50,50,4),[177,128,62,255],np.uint8)))


class RevealTests(unittest.TestCase):
    def engine(self, frames):
        now = [0.]
        def wait(seconds): now[0] += seconds
        e = SimpleNamespace(frame=Mock(side_effect=frames), status=Mock(), move_pointer=Mock(),
                            input=Mock(), motion=SimpleNamespace(now=lambda:now[0],wait=wait,key=Mock()))
        m = MemoryMatch(e,'Normal')
        m.title = Mock(return_value=True)
        return e,m

    def frame(self, item=False, clear=False):
        f=np.full((700,1000,4),[235,200,76,255],np.uint8)
        x,y=380,230
        f[y-25:y+25,x-25:x+25]=[177,128,62,255]
        if item: f[y-15:y+15,x-12:x+12]=[30,190,230,255]
        if clear:f[y-25:y+25,x-25:x+25]=[235,200,76,255]
        return f

    def test_capture_after_reveal_is_stable_and_not_the_card_back(self):
        back=self.frame()
        item=self.frame(item=True)
        e,m=self.engine([back,item,item])
        self.assertTrue(m.reveal(0))
        self.assertTrue(card_content(m.memory.images[0]))
        e.input.key.assert_called_once_with('lbutton',False)
        self.assertEqual(3,e.frame.call_count)

    def test_failed_click_is_not_saved_as_item(self):
        e,m=self.engine([])
        e.frame=Mock(return_value=self.frame())
        self.assertIsNone(m.reveal(0))
        self.assertEqual({},m.memory.images)
        e.input.key.assert_called_once_with('lbutton', False)

    def test_matching_cards_cleared_before_capture_reuse_first_item(self):
        before=self.frame()
        after=self.frame(clear=True)
        e,m=self.engine([before,after])
        m.memory.images[1]=self.frame(item=True)[205:255,355:405].copy()
        self.assertTrue(m.reveal(0,partner=1))
        np.testing.assert_array_equal(m.memory.images[0],m.memory.images[1])

    def test_board_closing_after_second_click_finishes(self):
        e,m=self.engine([self.frame(),self.frame(),self.frame()])
        m.title=Mock(side_effect=[True,False,False])
        self.assertFalse(m.reveal(0,partner=1))
        self.assertEqual({},m.memory.images)


class UnreadCardSolverTests(unittest.TestCase):
    def solver(self, outcomes, cached=False, last=False):
        e = SimpleNamespace(frame=Mock(return_value=np.zeros((700,1000,4),np.uint8)),
                            wait_for=Mock(return_value=True), motion=SimpleNamespace(wait=Mock()))
        m = MemoryMatch(e, 'Normal')
        m.title = Mock(return_value=True)
        m.last_chance = Mock(return_value=last)
        if cached:
            m.memory.images[0] = np.full((50,50,4), 100, np.uint8)
            m.memory.images[1] = m.memory.images[0].copy()
        sequence = iter(outcomes)
        def reveal(index, partner=None):
            result = next(sequence)
            if result is True:
                m.memory.images[index] = np.full((50,50,4), 20*index, np.uint8)
            return result
        m.reveal = Mock(side_effect=reveal)
        return m

    def test_unread_first_card_continues_to_other_cards(self):
        m = self.solver([None, True, True, False])
        m.solve()
        self.assertEqual([0,1,2,3], [c.args[0] for c in m.reveal.call_args_list])
        self.assertNotIn(0, m.memory.images)
        self.assertIsNone(m.reveal.call_args_list[1].kwargs['partner'])
        self.assertEqual(set(), m.memory.matched)

    def test_unread_second_card_does_not_discard_first_item(self):
        m = self.solver([True, None, True, False])
        m.solve()
        self.assertEqual([0,1,2,3], [c.args[0] for c in m.reveal.call_args_list])
        self.assertIn(0, m.memory.images)
        self.assertNotIn(1, m.memory.images)
        self.assertEqual(set(), m.memory.matched)

    def test_failed_known_pair_is_not_marked_matched_from_cached_images(self):
        m = self.solver([None, None, False], cached=True)
        m.solve()
        self.assertEqual([0,2,3], [c.args[0] for c in m.reveal.call_args_list])
        self.assertEqual(set(), m.memory.matched)

    def test_unread_selection_does_not_end_last_chance_early(self):
        m = self.solver([None, True, False], last=True)
        m.solve()
        self.assertEqual(3, m.reveal.call_count)

    def test_unresponsive_board_has_bounded_retries(self):
        m = self.solver([])
        m.reveal = Mock(return_value=None)
        m.engine.wait_for = Mock(side_effect=[True, False])
        with self.assertRaisesRegex(DetectionError, 'после повторных попыток'):
            m.solve()
        clicks = [c.args[0] for c in m.reveal.call_args_list]
        self.assertEqual(list(range(16)), clicks[:16])
        self.assertEqual(64, len(clicks))


if __name__=='__main__':unittest.main()
