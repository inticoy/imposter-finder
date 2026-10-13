"""Quaternius CC0 rigged human mesh, tinted and posed for the goal scene."""
import math
from functools import lru_cache
from pathlib import Path

import numpy as np
from imposter_finder.analysis.character_models import Model


@lru_cache(maxsize=1)
def model():
    return Model((Path(__file__).resolve().parents[2]/'assets/models/fc-player.glb').read_bytes())


def rotation(degrees,axis=0):
    q=[0.,0.,0.,math.cos(math.radians(degrees)/2)]
    q[axis]=math.sin(math.radians(degrees)/2)
    return q


@lru_cache(maxsize=128)
def posed(pose,phase):
    mesh=model();rotations={}
    walking=pose=='run'
    t=phase/math.tau*mesh.duration(True) if walking else 0
    if pose in ('kick','volley'):
        u=phase;contact=1/3
        angle=-35+115*min(u/contact,1) if u<contact else 80-85*min((u-contact)/(.7-contact),1)
        rotations={'UpperLeg.R':rotation(-angle),'LowerLeg.R':rotation(-max(0,35*(1-u/contact))),
                   'UpperArm.L':rotation(-30,2),'UpperArm.R':rotation(25,2)}
    elif pose in ('celebrate','header'):
        rotations={'UpperArm.L':rotation(-100,2),'UpperArm.R':rotation(100,2)}
    elif pose=='keeper':
        rotations={'UpperArm.L':rotation(-30,2),'UpperArm.R':rotation(30,2)}
    # Skinning yields a contiguous surface, including fingers, face, hair and shoes.
    return mesh.pose(t,walking,rotations)


def draw_player(draw,cam,anchor,kit,pose,phase,heading,dive,number):
    from imposter_finder.analysis.fc_highlight import norm,SKIN,HAIR,BOOT
    mesh=model();forward=norm(heading);right=(-forward[1],forward[0])
    scale=1.82/mesh.height;faces=[]
    for material,(verts,indices,base) in zip(mesh.part_materials,posed(pose,round(phase,2))):
        local=verts.copy()*scale
        local[:,1]-=mesh.floor*scale
        if pose=='dive':
            # Roll the entire rig along the actual dive side around its hips.
            a=math.radians(78)*abs(dive);sign=-1 if dive<0 else 1
            x,z=local[:,0].copy(),local[:,1]-.9
            local[:,0]=x*math.cos(a)+z*math.sin(a)*sign
            local[:,1]=z*math.cos(a)-x*math.sin(a)*sign
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
    if number is not None and forward[0]*cam.fw[0]+forward[1]*cam.fw[1]>.25:
        from imposter_finder.analysis.cards import font
        back=(anchor[0]-.16*forward[0],anchor[1]-.16*forward[1],anchor[2]+1.25)
        p,z=cam.proj(back)
        if p and cam.f/z>34:draw.text(p,str(number),font=font(max(8,round(cam.f/z*.22)),6),fill=kit['ink'],anchor='mm')
