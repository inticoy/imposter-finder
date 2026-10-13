"""Quaternius CC0 rigged human mesh, tinted and posed for the goal scene."""
import math
from functools import lru_cache
from pathlib import Path

import numpy as np
from imposter_finder.analysis.character_models import Model


def ease(u):
    u=min(max(u,0.),1.);return u*u*(3-2*u)


def clamp(v,lo,hi):
    return min(max(v,lo),hi)


@lru_cache(maxsize=1)
def model():
    return Model((Path(__file__).resolve().parents[2]/'assets/models/fc-player.glb').read_bytes())


def _rot(axis, degrees):
    """World rotation matrix about a unit axis."""
    x,y,z=np.asarray(axis,dtype=float)/max(np.linalg.norm(axis),1e-9);a=math.radians(degrees)
    c,s,C=math.cos(a),math.sin(a),1-math.cos(a)
    return np.array([[c+x*x*C,x*y*C-z*s,x*z*C+y*s],[y*x*C+z*s,c+y*y*C,y*z*C-x*s],[z*x*C-y*s,z*y*C+x*s,c+z*z*C]])


def _between(a, b):
    """Shortest rotation taking direction a onto b."""
    a=a/np.linalg.norm(a);b=b/np.linalg.norm(b);axis=np.cross(a,b);s=np.linalg.norm(axis)
    if s<1e-8:
        return np.eye(3)
    return _rot(axis,math.degrees(math.atan2(s,float(np.dot(a,b)))))


class Rig:
    """World-space posing on top of a baked clip: bones are aimed, not twisted by guessed local axes.
    Model space: +Y up, +Z the player's front, +X the player's left; metres (already scaled)."""

    def __init__(self, clip='HumanArmature|Man_Idle', time=0.0):
        self.mesh=model();self.k=1.82/self.mesh.height
        self.index={n.get('name'):i for i,n in enumerate(self.mesh.nodes)}
        self.local=self.mesh.locals(time,False,clip)
        self.refresh()
        self.base={i:m.copy() for i,m in self.w.items()}

    def refresh(self):
        self.w=self.mesh.world(self.local)

    def at(self, name):
        return self.w[self.index[name]][:3,3]*self.k

    def set_world(self, name, matrix):
        i=self.index[name];parent=self.mesh.parents[i]
        self.local[i]={'matrix':np.linalg.inv(self.w[parent])@matrix}
        self.refresh()

    def turn(self, name, axis, degrees):
        """Rotate a bone (and everything under it) about a world axis through its own head."""
        m=self.w[self.index[name]].copy();m[:3,:3]=_rot(axis,degrees)@m[:3,:3]
        self.set_world(name,m)

    def aim(self, name, direction):
        m=self.w[self.index[name]].copy();m[:3,:3]=_between(m[:3,1],np.asarray(direction,float))@m[:3,:3]
        self.set_world(name,m)

    def length(self, a, b):
        return float(np.linalg.norm(self.base[self.index[b]][:3,3]-self.base[self.index[a]][:3,3]))*self.k

    def limb(self, upper, lower, end, target, pole):
        """Two-bone IK: put `end` on target with the middle joint bent toward pole."""
        a=self.at(upper);t=np.asarray(target,float);l1,l2=self.length(upper,lower),self.length(lower,end)
        d=t-a;dist=float(np.clip(np.linalg.norm(d),abs(l1-l2)+1e-3,l1+l2-1e-3));d=d/max(np.linalg.norm(d),1e-9)
        cos=(l1*l1+dist*dist-l2*l2)/(2*l1*dist);sin=math.sqrt(max(0,1-cos*cos))
        side=np.asarray(pole,float)-np.dot(pole,d)*d
        side=side/max(np.linalg.norm(side),1e-9)
        mid=a+d*l1*cos+side*l1*sin
        self.aim(upper,mid-a);self.aim(lower,a+d*dist-self.at(lower))

    def leg(self, side, target, pole=(0,0,1), toe=0.0):
        """Foot bones are separate IK targets in this rig: carry them rigidly with the shin."""
        shin=self.index[f'LowerLeg.{side}'];before=self.w[shin].copy()
        self.limb(f'UpperLeg.{side}',f'LowerLeg.{side}',f'LowerLeg.{side}_end',target,pole)
        foot=self.w[shin]@np.linalg.inv(before)@self.w[self.index[f'Foot.{side}']]
        if toe:
            foot[:3,:3]=_rot((1,0,0),toe)@foot[:3,:3]
        self.set_world(f'Foot.{side}',foot)

    def arm(self, side, target, pole):
        self.limb(f'UpperArm.{side}',f'LowerArm.{side}',f'Palm.{side}',target,pole)

    def drop(self, metres, forward=0.0):
        """Lower the whole upper body (bent knees); legs are re-solved afterwards."""
        m=self.w[self.index['Body']].copy();m[1,3]-=metres/self.k;m[2,3]+=forward/self.k
        self.set_world('Body',m)

    def skin(self):
        return self.mesh.skin(self.w)


def _kick(rig, u, volley=False):
    contact=1/3
    back,hit,follow=np.array([-.13,.42,-.55]),np.array([-.12,.13 if not volley else .72,.32]),np.array([-.08,.62 if not volley else 1.05,.62])
    if u<contact: foot=back+(hit-back)*ease(u/contact)
    elif u<.7: foot=hit+(follow-hit)*ease((u-contact)/(.7-contact))
    else: foot=follow+(np.array([-.14,.03,.15])-follow)*ease((u-.7)/.3)
    swing=(u-contact)*3
    rig.drop(.06 if not volley else .1)
    rig.turn('Abdomen',(1,0,0),-6-8*clamp(swing,0,1))  # lean back over the ball
    if volley:
        rig.turn('Body',(0,0,1),-24)  # side-on to meet a dropping ball
    rig.leg('L',(.16,.03,.08),pole=(0,0,1))
    rig.leg('R',foot,pole=(0,.2,1),toe=-35 if u<.75 else -10)
    sh=rig.at('Shoulder.L');rig.arm('L',sh+np.array([.55,.05,.15-.25*clamp(swing,0,1)]),(0,-1,-.3))
    sh=rig.at('Shoulder.R');rig.arm('R',sh+np.array([-.35,-.25,-.2+.45*clamp(swing,0,1)]),(0,-1,-.4))


def _header(rig, u):
    """u: 0 take-off, .5 contact, 1 landing. Arched back, then the neck and torso snap through the ball."""
    snap=clamp((u-.38)/.2,0,1)
    rig.turn('Abdomen',(1,0,0),-14+30*snap)
    rig.turn('Neck',(1,0,0),-12+26*snap)
    tuck=math.sin(math.pi*clamp(u,0,1))
    hip=rig.at('UpperLeg.L')[1]
    rig.leg('L',(.15,max(.03,hip-.78+.3*tuck),-.12-.2*tuck),pole=(0,0,1),toe=-20*tuck)
    rig.leg('R',(-.15,max(.03,hip-.72+.4*tuck),-.02-.32*tuck),pole=(0,0,1),toe=-25*tuck)
    for side,x in (('L',1),('R',-1)):
        sh=rig.at(f'Shoulder.{side}')
        # Elbows out at shoulder height for balance and leverage, never reaching at the ball.
        rig.arm(side,sh+np.array([x*.40,-.16+.1*(1-snap),.04-.12*snap]),(x*.3,-.3,-1))


def _keeper(rig):
    rig.drop(.16,.03)
    rig.turn('Abdomen',(1,0,0),16)
    rig.leg('L',(.27,.03,.02),pole=(.3,0,1));rig.leg('R',(-.27,.03,.02),pole=(-.3,0,1))
    for side,x in (('L',1),('R',-1)):
        sh=rig.at(f'Shoulder.{side}')
        rig.arm(side,sh+np.array([x*.15,-.32,.38]),(x,-1,0))


def _dive(rig, reach, high):
    """Stretched out sideways (+X = the player's left): both palms beyond the head toward the ball."""
    side=1 if reach>=0 else -1;k=abs(reach)
    rig.turn('Abdomen',(0,0,1),side*14*k)
    for name,x in (('L',1),('R',-1)):
        sh=rig.at(f'Shoulder.{name}')
        up=np.array([side*.18+x*.07,.55+.1*high,.12])
        rest=np.array([x*.2,-.5,.15])
        rig.arm(name,sh+rest+(up-rest)*k,(x*.2,0,-1))
    hip=rig.at('UpperLeg.L')[1]
    rig.leg('L' if side<0 else 'R',(-side*.22,hip-.85,.0),pole=(0,0,1))  # push-off leg extended
    rig.leg('R' if side<0 else 'L',(side*.05,hip-.55,.1),pole=(side*.4,0,1),toe=-20)  # lead knee drives up


@lru_cache(maxsize=256)
def posed(pose,phase,dive=0.0):
    if pose=='run':
        mesh=model()
        return mesh.skin(mesh.world(mesh.locals(phase/math.tau*mesh.duration(True),True))),None
    run=model().duration(True)*phase/math.tau
    rig=Rig('HumanArmature|Man_Run',run) if pose=='celebrate' else Rig()
    if pose in ('kick','volley'):_kick(rig,phase,pose=='volley')
    elif pose=='header':_header(rig,phase)
    elif pose=='keeper':_keeper(rig)
    elif pose=='dive':_dive(rig,dive,phase)
    elif pose=='celebrate':  # 달리면서 두 팔을 활짝 (비행기 세리머니)
        rig.turn('Abdomen',(0,0,1),8*math.sin(phase))
        for side,x in (('L',1),('R',-1)):
            sh=rig.at(f'Shoulder.{side}');rig.arm(side,sh+np.array([x*.68,.06-.1*x*math.sin(phase),-.08]),(0,1,-1))
    elif pose=='cheer':  # 멈춰 서서 주먹 쥔 두 팔을 하늘로, 무릎은 살짝
        pump=.5+.5*math.cos(phase*7)
        rig.drop(.03*pump)
        rig.leg('L',(.16,.03,.02),pole=(0,0,1));rig.leg('R',(-.16,.03,-.03),pole=(0,0,1))
        rig.turn('Neck',(1,0,0),-14)
        for side,x in (('L',1),('R',-1)):
            sh=rig.at(f'Shoulder.{side}');rig.arm(side,sh+np.array([x*.3,.52-.12*pump,.08]),(x,-.3,-1))
    elif pose=='wall':
        for side,x in (('L',1),('R',-1)):
            sh=rig.at(f'Shoulder.{side}');rig.arm(side,sh+np.array([-x*.12,-.48,.18]),(x,0,1))
    pivot=(rig.at('UpperLeg.L')+rig.at('UpperLeg.R'))/2
    return rig.skin(),pivot


def head_point(phase=.5):
    """Forehead in model metres (x left, y up, z forward) for the header pose: where the ball must meet."""
    rig=Rig();_header(rig,phase)
    neck,top=rig.at('Head'),rig.at('Head_end')
    return neck+(top-neck)*.62+np.array([0,0,.09])


def draw_player(draw,cam,anchor,kit,pose,phase,heading,dive,number):
    from imposter_finder.analysis.fc_highlight import norm,SKIN,HAIR,BOOT
    mesh=model();forward=norm(heading);right=(-forward[1],forward[0])
    scale=1.82/mesh.height;faces=[]
    # The keeper's dive is signed along world y; in model space +X is the `right` vector here.
    reach=round(dive*(1 if right[1]>=0 else -1),2) if pose=='dive' else 0.0
    parts,pivot=posed(pose,round(phase,2) if pose!='dive' else 0.0,reach)
    for material,(verts,indices,base) in zip(mesh.part_materials,parts):
        local=verts.copy()*scale
        if pose=='dive':
            # Roll the body about its hips toward the ball; the anchor is the hip centre in flight.
            a=-math.radians(80)*reach;local-=pivot
            x,y=local[:,0].copy(),local[:,1].copy()
            local[:,0]=x*math.cos(a)-y*math.sin(a)
            local[:,1]=x*math.sin(a)+y*math.cos(a)
        else:
            local[:,1]-=mesh.floor*scale
        world=np.column_stack((anchor[0]+local[:,2]*forward[0]+local[:,0]*right[0],
                               anchor[1]+local[:,2]*forward[1]+local[:,0]*right[1],anchor[2]+local[:,1]))
        delta=world-np.asarray(cam.pos)
        # Cam's basis is exactly the basis used for the pitch and the ball.
        xyz=np.column_stack((delta@np.asarray(cam.r),delta@np.asarray(cam.u),delta@np.asarray(cam.fw)))
        valid=xyz[:,2]>.3
        screen=np.column_stack((cam.cx+cam.f*xyz[:,0]/np.maximum(xyz[:,2],.3),cam.cy-cam.f*xyz[:,1]/np.maximum(xyz[:,2],.3)))
        triangles=screen[indices];depth=xyz[indices,2].mean(axis=1)
        normal=np.cross(world[indices[:,1]]-world[indices[:,0]],world[indices[:,2]]-world[indices[:,0]])
        lighting=.67+.33*np.abs(normal@np.asarray(norm((-.4,-.5,1))))/np.maximum(np.linalg.norm(normal,axis=1),1e-8)
        color={'Shirt':kit['shirt'],'Shirt2':kit['ink'],'Pants':kit['shorts'],'Shoes':BOOT,
               'Socks':kit['socks'],'Skin':SKIN,'Hair':HAIR,'Hair2':HAIR,'Eyes':(24,24,28)}[material]
        colors=np.tile(color,(len(indices),1)).astype(float)
        if material=='Pants':
            height=local[indices,1].mean(axis=1)
            colors[height<.72]=SKIN;colors[height<.40]=kit['socks']
        colors=np.clip(colors*lighting[:,None],0,255).astype(np.uint8)
        for i in np.flatnonzero(valid[indices].all(axis=1)):
            faces.append((depth[i],triangles[i],tuple(int(c) for c in colors[i])))
    for _,points,color in sorted(faces,key=lambda p:-p[0]):draw.polygon([tuple(p) for p in points],fill=color)
    # Only the back carries a shirt number; facial mesh defines the front.
    if number is not None and pose!='dive' and forward[0]*cam.fw[0]+forward[1]*cam.fw[1]>.5:
        from imposter_finder.analysis.cards import font
        back=(anchor[0]-.16*forward[0],anchor[1]-.16*forward[1],anchor[2]+1.25)
        p,z=cam.proj(back)
        if p and cam.f/z>34:draw.text(p,str(number),font=font(max(8,round(cam.f/z*.22)),6),fill=kit['ink'],anchor='mm')
