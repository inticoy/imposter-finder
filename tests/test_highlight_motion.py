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

class HighlightEvidenceRegressionTests(unittest.TestCase):
    def test_close_assist_is_not_discarded(self):
        shot={'spId':101,'goalTime':4800,'x':.895,'y':.5,'type':6,
              'assist':True,'assistSpId':102,'assistX':.89,'assistY':.5}
        scene=F.Scene(shot,8,9)
        self.assertTrue(scene.assisted)
        self.assertEqual(scene.A,F._world(.89,.5))
        self.assertIn('passer',[p[0] for p in scene.players(0)])

    def test_same_club_uses_distinct_shirts(self):
        from imposter_finder.analysis.fc_report import Team,Friend
        team=lambda name:Team({'nickname':name},Friend(name,None),None,'맨체스터 유나이티드',(214,30,36))
        home,away=F._match_kits(team('A'),team('B'))
        self.assertGreater(sum(abs(a-b) for a,b in zip(home['shirt'],away['shirt'])),180)

    def test_pubg_camera_uses_camera_space_distance(self):
        cam=Camera((1000,1000,30),0,58,300)
        self.assertAlmostEqual(cam.project(1000,1000,30)[2],300)
        # Elevated point is closer along the pitched optical axis.
        self.assertAlmostEqual(cam.project(1000,1000,40)[2],300-10*math.sin(math.radians(58)))
        self.assertAlmostEqual(cam.project(1000,1000,40)[1],280-cam.focal*10*math.cos(math.radians(58))/(300-10*math.sin(math.radians(58))))

    def test_pubg_flat_terrain_depth_agrees_with_actor_projection(self):
        from types import SimpleNamespace
        import numpy as np
        from imposter_finder.analysis import pubg_highlight as P
        cam=Camera((0,0,0),25,58,100)
        cam.w,cam.h=80,45;cam.focal*=.08;cam.v0=cam.h/2-cam.focal*math.tan(math.radians(58))
        n=16
        terrain=SimpleNamespace(cx=0,cy=0,half=150,step=300/n,bottom=-5,zmax=0,
                                h=np.zeros((n,n)),rgb=np.full((n,n,3),100,dtype=np.float32),wall_lit=np.ones((n,n)))
        with patch.object(P,'TEX',n):
            _,depth=P.render_terrain(terrain,cam)
        for x,y in ((0,0),(20,10),(-20,-15)):
            sx,sy,z=cam.project(x,y,0)
            self.assertAlmostEqual(depth[int(sy),int(sx)],z,delta=3)

class FootballAssetTests(unittest.TestCase):
    def test_real_mesh_has_rig_and_kit_materials(self):
        from imposter_finder.analysis.fc_player_mesh import model,posed
        mesh=model()
        self.assertIn('Shirt',mesh.part_materials)
        self.assertIn('Skin',mesh.part_materials)
        self.assertIn('Eyes',mesh.part_materials)
        idle,_=posed('stand',0);run,_=posed('run',1)
        self.assertGreater(sum(len(indices) for _,indices,_ in idle),1000)
        self.assertGreater(abs(idle[0][0]-run[0][0]).max(),.01)

    def test_kicking_foot_stays_on_the_shin_and_header_meets_the_forehead(self):
        import numpy as np
        from imposter_finder.analysis.fc_player_mesh import Rig,_kick,head_point
        rig=Rig();_kick(rig,1/3)
        # The rig's feet are free IK bones: they must travel with the shin, not stay planted.
        self.assertLess(np.linalg.norm(rig.at('Foot.R')-rig.at('LowerLeg.R_end')),.12)
        self.assertGreater(rig.at('Foot.R')[1],.08)
        head=head_point(.5)
        self.assertAlmostEqual(head[1],F.FOREHEAD[1],delta=.04)
        self.assertAlmostEqual(head[2],F.FOREHEAD[0],delta=.04)

    def test_rig_rendering_does_not_depend_on_procedural_fallback(self):
        from imposter_finder.analysis.fc_player_mesh import draw_player
        im=Image.new('RGB',(F.VW*F.S,F.VH*F.S))
        cam=F.Cam((88,22,4),(94,34,1),40)
        draw_player(ImageDraw.Draw(im),cam,(94,34,0),F._kit((214,30,36)), 'stand',0,(1,0),0,11)
        self.assertIsNotNone(im.getbbox())


if __name__=='__main__':
    unittest.main()
