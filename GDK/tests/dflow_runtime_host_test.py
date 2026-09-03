from __future__ import annotations
import subprocess, tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
with tempfile.TemporaryDirectory() as td:
    td=Path(td)
    test=td/'test.c'
    test.write_text(r'''
#include <assert.h>
#include <stdint.h>
#include "dms_flow.h"
static int enter0, enter1, enter2, update1;
static void e0(void){enter0++;}
static void e1(void){enter1++;}
static void e2(void){enter2++;}
static void u1(void){update1++;}
static const DmsFlowStateDef states[]={
 {"A",0,e0,0,0},{"B",1,e1,u1,0},{"C",2,e2,0,0}
};
static const DmsFlowTransitionDef trans[]={
 {0,1,0,2,100,0,0},
 {1,2,7,0,100,0,0}
};
const DmsFlowDefinition dms_flow_definition={states,3,trans,2,0};
int main(void){
 FLOW_init(0); assert(FLOW_current()==0 && enter0==1);
 FLOW_update(); assert(FLOW_transitionPending());
 FLOW_update(); assert(FLOW_current()==0);
 FLOW_update(); assert(FLOW_current()==1 && enter1==1);
 FLOW_update(); assert(update1==1 && FLOW_current()==1);
 FLOW_emit(7); FLOW_update(); assert(FLOW_current()==2 && enter2==1);
 return 0;
}
''',encoding='utf-8')
    exe=td/'flow_test'
    cp=subprocess.run(['gcc','-std=c11','-I',str(ROOT/'GDK'/'include'),str(ROOT/'GDK'/'lib'/'src'/'dms_flow.c'),str(test),'-o',str(exe)],capture_output=True,text=True)
    assert cp.returncode==0, cp.stderr
    cp=subprocess.run([str(exe)],capture_output=True,text=True)
    assert cp.returncode==0, cp.stderr
print('PASS libdms FLOW runtime host simulation: ENTER/AUTO delay/event/UPDATE/transition')
