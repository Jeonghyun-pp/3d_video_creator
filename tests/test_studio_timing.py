import copy
import unittest
from studio.common import StudioError
from studio.project import default_shot
from studio.timing import resolve_actions

class TimingTests(unittest.TestCase):
    def shot(self):
        s=default_shot('test',180,{'request':'test'})
        s['narration']['cues']=[{'cue_id':'first','spoken_text':'열고','display_text':'열고','character_range':[0,2]},
                               {'cue_id':'second','spoken_text':'열고','display_text':'열고','character_range':[4,6]}]
        s['actions']=[{'action_id':'open','type':'peel','targets':[{'instance_id':'model','part_id':'skin'}],
           'start_frame':0,'end_frame':60,'easing':'linear','params':{'direction_source':'asset','distance_m':1,'order':'asset_order'},
           'time_binding':{'start_cue_id':'second','end_cue_id':'second','start_offset_frames':2,'end_offset_frames':5}}]
        return s
    def test_repeated_word_uses_second_cue_and_keeps_input(self):
        s=self.shot();before=copy.deepcopy(s)
        cues=[{'cue_id':'first','start_frame':4,'end_frame':14},{'cue_id':'second','start_frame':40,'end_frame':55}]
        new,changes=resolve_actions(s,cues)
        self.assertEqual(s,before)
        self.assertEqual((new['actions'][0]['start_frame'],new['actions'][0]['end_frame']),(42,60))
        self.assertEqual(len(changes),1)
        self.assertEqual(resolve_actions(new,cues)[1],[])
    def test_missing_or_outside_cues_fail(self):
        s=self.shot()
        for cues in ([],[{'cue_id':'second','start_frame':170,'end_frame':180}]):
            with self.assertRaises(StudioError):resolve_actions(s,cues)
    def test_fixed_frame_action_unchanged(self):
        s=self.shot();del s['actions'][0]['time_binding']
        self.assertEqual(resolve_actions(s,[]),(s,[]))
if __name__=='__main__':unittest.main()
