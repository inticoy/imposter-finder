import json
import struct
import unittest
import numpy as np
from imposter_finder.analysis.character_models import Model


def model_fixture():
    chunks=[];views=[];accessors=[]
    def accessor(data,kind,component):
        array=np.asarray(data,dtype={5126:'<f4',5123:'<u2'}[component]);raw=array.tobytes()
        offset=sum(len(c) for c in chunks);padding=(-offset)%4
        if padding:chunks.append(b'\0'*padding);offset+=padding
        chunks.append(raw);views.append({'buffer':0,'byteOffset':offset,'byteLength':len(raw)})
        accessors.append({'bufferView':len(views)-1,'componentType':component,'count':len(array),'type':kind})
        return len(accessors)-1
    positions=accessor([[0,0,0],[1,0,0],[0,1,0],[0,-1000,0]],'VEC3',5126)
    uv=accessor([[0,0]]*4,'VEC2',5126)
    joints=accessor([[0,0,0,0]]*4,'VEC4',5123)
    weights=accessor([[1,0,0,0]]*4,'VEC4',5126)
    indices=accessor([0,1,2],'SCALAR',5123)
    inverse=accessor([np.eye(4).flatten()],'MAT4',5126)
    times=accessor([0,1],'SCALAR',5126)
    translations=accessor([[0,0,0],[1,0,0]],'VEC3',5126)
    doc={'asset':{'version':'2.0'},'nodes':[{'mesh':0,'skin':0},{'name':'root'}],
         'skins':[{'joints':[1],'inverseBindMatrices':inverse}],
         'meshes':[{'primitives':[{'indices':indices,'attributes':{'POSITION':positions,'TEXCOORD_0':uv,'JOINTS_0':joints,'WEIGHTS_0':weights}}]}],
         'materials':[{}],'bufferViews':views,'accessors':accessors,
         'animations':[{'name':'Idle1','samplers':[{'input':times,'output':translations}],
                        'channels':[{'sampler':0,'target':{'node':1,'path':'translation'}}]}]}
    binary=b''.join(chunks);binary+=b'\0'*((-len(binary))%4)
    doc['buffers']=[{'byteLength':len(binary)}]
    body=json.dumps(doc).encode();body+=b' '*((-len(body))%4)
    return struct.pack('<III',0x46546c67,2,28+len(body)+len(binary))+struct.pack('<II',len(body),0x4e4f534a)+body+struct.pack('<II',len(binary),0x004e4942)+binary


class ModelTests(unittest.TestCase):
    def test_skinning_applies_interpolated_bone_transform(self):
        mesh=Model(model_fixture())
        self.assertAlmostEqual(mesh.pose(.5,False)[0][0][0,0],.5)

    def test_unused_vertices_do_not_change_ground_anchor(self):
        mesh=Model(model_fixture())
        self.assertEqual(mesh.floor,0)
        self.assertEqual(mesh.height,1)
