import math
import unittest
from unittest.mock import patch
from PIL import Image, ImageDraw
from imposter_finder.analysis import fc_highlight as F
from imposter_finder.analysis.pubg_highlight import Camera
from imposter_finder.analysis import lol_highlight as L


class FootballMotionTests(unittest.TestCase):
    def scene(self):
        return F.Scene({'spId':101,'goalTime':4800,'x':.895,'y':.5,'type':6,'assist':False},8,9)

    def test_stationary_player_does_not_keep_running(self):
        scene=self.scene()
        a=next(p for p in scene.players(scene.T_LINE+.1) if p[0]=='mate')
        b=next(p for p in scene.players(scene.T_LINE+.3) if p[0]=='mate')
        self.assertEqual(a[2],b[2])
        self.assertEqual(a[3],'stand')
        self.assertEqual(a[5],b[5])

    def test_stance_foot_stays_on_ground_as_root_advances(self):
        a,b=.1,.3
        foot_a=F._pose('run',a)['feet'][0]
        foot_b=F._pose('run',b)['feet'][0]
        root_distance=(b-a)/math.tau*1.8
        self.assertAlmostEqual(foot_a[0],foot_b[0]+root_distance)
        self.assertEqual(foot_a[1],foot_b[1])

    def test_shot_contact_has_no_root_teleport(self):
        scene=self.scene();t=scene.T_SHOT
        p=next(p[2] for p in scene.players(t-.0001) if p[0]=='scorer')
        q=next(p[2] for p in scene.players(t+.0001) if p[0]=='scorer')
        self.assertLess(math.dist(p,q),.01)
        phase=scene.action_phase('scorer','kick',t)
        self.assertAlmostEqual(phase,1/3)
        before=F._pose('kick',phase-.001)['feet'][1]
        after=F._pose('kick',phase+.001)['feet'][1]
        self.assertLess(math.dist(before,after),.01)

    def test_world_heading_changes_body_geometry(self):
        cam=F.Cam((88,22,4),(94,34,1),40)
        images=[]
        for heading in ((1,0),(0,1)):
            im=Image.new('RGB',(F.VW*F.S,F.VH*F.S))
            F._figure(ImageDraw.Draw(im),cam,(94,34,0),F._kit((214,30,36)), 'stand',0,1,heading=heading)
            images.append(im.tobytes())
        self.assertNotEqual(images[0],images[1])


class LoLPositionEvidenceTests(unittest.TestCase):
    def test_ornn_purchase_does_not_send_him_to_fountain(self):
        info={'participants':[{'participantId':1,'teamId':100,'championName':'Ornn'}]}
        def frame(t,events=()):
            return {'timestamp':t,'events':list(events),'participantFrames':{'1':{'position':{'x':8000,'y':8000},'championStats':{'health':100}}}}
        timeline={'info':{'frames':[frame(0),frame(60000,[{'type':'ITEM_PURCHASED','timestamp':30000,'participantId':1}])]}}
        with patch.object(L,'route',side_effect=lambda a,b:[a,b]),patch.object(L,'snap',side_effect=lambda p:p):
            tracks,_=L.build_tracks(info,timeline,0,60000,(8000,8000),'unused')
        self.assertEqual(tracks[1].at(30000),(8000,8000))


class PubgCameraTests(unittest.TestCase):
    def test_high_angle_keeps_target_and_height_order(self):
        cam=Camera((1000,1000,30),0,58,300)
        foot=cam.project(1000,1000,30)
        head=cam.project(1000,1000,32)
        self.assertAlmostEqual(foot[0],500)
        self.assertAlmostEqual(foot[1],280)
        self.assertLess(head[1],foot[1])


if __name__=='__main__':
    unittest.main()
