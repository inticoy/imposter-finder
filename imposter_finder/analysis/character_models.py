"""GLB skinning and animated champion meshes rendered into directional sprites.

Khada's public GLB conversion supplies the mesh/rig; timeline paths still supply
only inferred movement. These are base skins, not the player's selected skin.
"""
from __future__ import annotations

import json
import math
import struct
import urllib.request
from functools import lru_cache
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

CACHE = Path(__file__).resolve().parents[2] / 'data/assets/lol/models'
SOURCE = 'https://cdn.modelviewer.lol/lol/models'


def _matrix(t, q, s):
    x,y,z,w=q
    m=np.eye(4,dtype=np.float32)
    m[:3,:3]=np.array([[1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w)],
                      [2*(x*y+z*w),1-2*(x*x+z*z),2*(y*z-x*w)],
                      [2*(x*z-y*w),2*(y*z+x*w),1-2*(x*x+y*y)]])*np.asarray(s)[None,:]
    m[:3,3]=t
    return m


class Model:
    def __init__(self, raw: bytes):
        magic,version,length=struct.unpack_from('<III',raw)
        if magic!=0x46546c67 or version!=2 or length!=len(raw):
            raise ValueError('Invalid GLB')
        n=struct.unpack_from('<I',raw,12)[0]
        self.doc=json.loads(raw[20:20+n]);self.bin=raw[28+n:]
        self.nodes=self.doc['nodes'];self.parents={}
        for i,node in enumerate(self.nodes):
            for child in node.get('children',[]):self.parents[child]=i
        self.parts=[]
        self.part_materials=[]
        for node in self.nodes:
            if 'mesh' not in node:continue
            skin=self.doc['skins'][node['skin']]
            inverse=self.access(skin['inverseBindMatrices']).reshape(-1,4,4).transpose(0,2,1)
            for p in self.doc['meshes'][node['mesh']]['primitives']:
                material=self.doc['materials'][p.get('material',0)]
                if material.get('extras',{}).get('visible') is False:continue
                attrs=p['attributes'];verts=self.access(attrs['POSITION']);tri=self.access(p['indices']).reshape(-1,3)
                uv=self.access(attrs['TEXCOORD_0']) if 'TEXCOORD_0' in attrs else None
                texture=material.get('pbrMetallicRoughness',{}).get('baseColorTexture',{}).get('index')
                if texture is not None:
                    image=self.doc['images'][self.doc['textures'][texture]['source']]
                    view=self.doc['bufferViews'][image['bufferView']];off=view.get('byteOffset',0)
                    pixels=np.asarray(Image.open(BytesIO(self.bin[off:off+view['byteLength']])).convert('RGBA'))
                    texuv=uv[tri].mean(axis=1)%1
                    colors=pixels[np.minimum((texuv[:,1]*pixels.shape[0]).astype(int),pixels.shape[0]-1),
                                  np.minimum((texuv[:,0]*pixels.shape[1]).astype(int),pixels.shape[1]-1)]
                else:
                    factor=material.get('pbrMetallicRoughness',{}).get('baseColorFactor',[.7,.7,.7,1])
                    colors=np.tile((np.asarray(factor)*255).astype(np.uint8),(len(tri),1))
                self.part_materials.append(material.get('name',''))
                self.parts.append((verts,tri,self.access(attrs['JOINTS_0']).astype(int),self.access(attrs['WEIGHTS_0']),
                                   skin['joints'],inverse,colors))
        self.animations={a['name']:a for a in self.doc.get('animations',[])}
        posed=self.pose(0,False)
        xyz=np.concatenate([v[np.unique(indices)] for v,indices,_ in posed]);self.floor=float(xyz[:,1].min())
        self.height=max(float(np.ptp(xyz[:,1])),.01)

    @lru_cache(maxsize=2048)
    def access(self, i):
        a=self.doc['accessors'][i];v=self.doc['bufferViews'][a['bufferView']]
        dtype={5120:'i1',5121:'u1',5122:'<i2',5123:'<u2',5125:'<u4',5126:'<f4'}[a['componentType']]
        size={'SCALAR':1,'VEC2':2,'VEC3':3,'VEC4':4,'MAT4':16}[a['type']]
        offset=v.get('byteOffset',0)+a.get('byteOffset',0);item=np.dtype(dtype).itemsize
        data=np.ndarray((a['count'],size),dtype=dtype,buffer=self.bin,offset=offset,
                        strides=(v.get('byteStride',size*item),item)).copy()
        if a.get('normalized'):
            limit=np.iinfo(np.dtype(dtype)).max;data=np.maximum(data.astype(np.float32)/limit,-1)
        return data

    def animation(self, walking):
        wanted='run' if walking else 'idle'
        name=next((n for n in self.animations if n.lower() in ('run_base','run','idle1','idle') and wanted in n.lower()),None)
        name=name or next((n for n in self.animations if n.lower().startswith(wanted) or n.lower().endswith('_'+wanted)),None)
        return self.animations.get(name)

    def action(self, kind):
        """Named clip for 'attack' / 'death', else None."""
        names=sorted(self.animations,key=lambda n:(len(n),n))
        if kind=='attack':
            return next((n for n in names if n.lower().split('/')[-1].startswith(('attack1','attack'))),None) or \
                next((n for n in names if 'attack' in n.lower()),None)
        if kind=='death':
            return next((n for n in names if 'death' in n.lower()),None)
        return None

    def duration(self, walking, name=None):
        animation=self.animations.get(name) if name else self.animation(walking)
        return max((float(self.access(s['input'])[-1,0]) for s in animation['samplers']),default=1) if animation else 1

    def locals(self, time, walking=False, name=None):
        """Local TRS per node at `time` of an animation (by name, else run/idle)."""
        transforms=[{k:np.array(n.get(k,default),dtype=np.float32) for k,default in
                     (('translation',[0,0,0]),('rotation',[0,0,0,1]),('scale',[1,1,1]))} for n in self.nodes]
        anim=self.animations.get(name) if name else self.animation(walking)
        if anim:
            for channel in anim['channels']:
                target=channel['target'];path=target['path']
                if path not in ('translation','rotation','scale'):continue
                sample=anim['samplers'][channel['sampler']]
                ts=self.access(sample['input']).ravel();values=self.access(sample['output'])
                t=time%float(ts[-1]) if ts[-1]>0 else 0
                right=min(int(np.searchsorted(ts,t,side='right')),len(ts)-1);left=max(0,right-1)
                u=(t-ts[left])/max(float(ts[right]-ts[left]),1e-8)
                a,b=values[left],values[right]
                if path=='rotation' and np.dot(a,b)<0:b=-b
                val=a*(1-u)+b*u
                if path=='rotation':val=val/max(float(np.linalg.norm(val)),1e-8)
                transforms[target['node']][path]=val
        return transforms

    def world(self, transforms):
        world={}
        def matrix(i):
            if i not in world:
                node=self.nodes[i];x=transforms[i]
                local=x['matrix'] if 'matrix' in x else (np.asarray(node['matrix'],dtype=np.float32).reshape(4,4).T if 'matrix' in node else _matrix(x['translation'],x['rotation'],x['scale']))
                world[i]=matrix(self.parents[i])@local if i in self.parents else local
            return world[i]
        for i in range(len(self.nodes)):matrix(i)
        return world

    def skin(self, world):
        result=[]
        for verts,tri,joints,weights,bones,inverse,colors in self.parts:
            skin=np.stack([world[i] for i in bones])@inverse
            weighted=(skin[joints]*weights[:,:,None,None]).sum(axis=1)
            homogeneous=np.column_stack((verts,np.ones(len(verts))))
            posed=np.einsum('nij,nj->ni',weighted,homogeneous)[:,:3]
            result.append((posed,tri,colors))
        return result

    def pose(self, time, walking, rotations=None):
        transforms=self.locals(time,walking)
        if rotations:
            for i,node in enumerate(self.nodes):
                if node.get('name') in rotations:
                    # Local post-rotation keeps the model's original bone axes.
                    a=transforms[i]['rotation'];b=np.asarray(rotations[node['name']])
                    xyz=a[3]*b[:3]+b[3]*a[:3]+np.cross(a[:3],b[:3])
                    transforms[i]['rotation']=np.r_[xyz,a[3]*b[3]-np.dot(a[:3],b[:3])]
        return self.skin(self.world(transforms))


@lru_cache(maxsize=32)
def model(champion: str, champion_id: int) -> Model | None:
    path=CACHE/f'{champion_id}.glb'
    try:
        if not path.exists():
            CACHE.mkdir(parents=True,exist_ok=True)
            req=urllib.request.Request(f'{SOURCE}/{champion.lower()}/{champion_id*1000}/model.glb',headers={'User-Agent':'imposter-finder/0.1'})
            with urllib.request.urlopen(req,timeout=20) as response:raw=response.read(40*1024*1024)
            parsed=Model(raw)
            path.write_bytes(raw)
            return parsed
        return Model(path.read_bytes())
    except (OSError,ValueError,KeyError,struct.error):
        return None


@lru_cache(maxsize=512)
def sprite(champion: str, champion_id: int, angle: int, pitch: int, phase: int, walking: bool,
           action: str | None = None) -> Image.Image | None:
    """phase: 0..7 through the clip (run/idle loop, or one attack swing / the death fall when `action` is set)."""
    mesh=model(champion,champion_id)
    if mesh is None:return None
    clip=mesh.action(action) if action else None
    # Local GLTF: +Y up, +Z forward. Rotate into the viewer's ground plane.
    yaw=math.radians(angle*22.5);elevation=math.radians(pitch*5)
    scale=100/mesh.height;polygons=[]
    if clip:
        # Death holds its last frame; an attack swings once per cycle.
        span=mesh.duration(False,clip)
        when=min(phase/7,1)*span*.999 if action=='death' else phase/8*span*.6  # swing, not the idle tail
        posed=mesh.skin(mesh.world(mesh.locals(when,False,clip)))
    else:
        posed=mesh.pose(phase/8*mesh.duration(walking),walking)
    for verts,indices,colors in posed:
        x=verts[:,0]*math.cos(yaw)+verts[:,2]*math.sin(yaw)
        depth=-verts[:,0]*math.sin(yaw)+verts[:,2]*math.cos(yaw)
        z=verts[:,1]-mesh.floor
        screen=np.column_stack((160+x*scale,240-(z*math.cos(elevation)-depth*math.sin(elevation))*scale))
        faces=screen[indices];distance=(depth*math.cos(elevation)+z*math.sin(elevation))[indices].mean(axis=1)
        normal=np.cross(verts[indices[:,1]]-verts[indices[:,0]],verts[indices[:,2]]-verts[indices[:,0]])
        lighting=.76+.24*np.abs(normal[:,1])/np.maximum(np.linalg.norm(normal,axis=1),1e-8)
        rgb=np.clip(colors[:,:3]*lighting[:,None],0,255).astype(np.uint8)
        a,b=faces[:,1]-faces[:,0],faces[:,2]-faces[:,0]
        area=np.abs(a[:,0]*b[:,1]-a[:,1]*b[:,0])
        for i in np.flatnonzero((colors[:,3]>80)&(area>.15)):
            polygons.append((distance[i],faces[i],tuple(int(v) for v in rgb[i])+(int(colors[i,3]),)))
    image=Image.new('RGBA',(320,320));draw=ImageDraw.Draw(image)
    for _,points,color in sorted(polygons,key=lambda p:p[0]):draw.polygon([tuple(p) for p in points],fill=color)
    return image
