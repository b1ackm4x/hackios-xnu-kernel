#!/usr/bin/env python3
"""Run the actual layer compositor and scaler on host memory under ASan/UBSan."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
ROOT=Path(__file__).resolve().parents[3]
spec=importlib.util.spec_from_file_location('fences',Path(__file__).with_name('test-lemonade-display-fences.py'))
fences=importlib.util.module_from_spec(spec);spec.loader.exec_module(fences)
s=(ROOT/'xnu/files/iokit/Kernel/MiniIOMobileFramebuffer.cpp').read_text()
# Include the production functions, not a second implementation of the renderer.
inline=s[s.index('static inline uint32_t\nblend('):s.index('/*\n * The rows of one layer')]
row=s[s.index('struct RowJob {'):s.index('\nvoid\nIOMobileFramebuffer::drawRows')]
pre=r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <vector>
#include <random>
#include <functional>
#define SWAP_LAYERS 4
#define SWAP_ID 0x50
#define SWAP_SURFACE(n) (0x54+4*(n))
#define SWAP_SOURCE(n) (0x64+16*(n))
#define SWAP_DEST(n) (0xc4+16*(n))
#define FOURCC(a,b,c,d) ((uint32_t)(a)<<24|(uint32_t)(b)<<16|(uint32_t)(c)<<8|(uint32_t)(d))
#define IONew(type,n) new type[n]
#define IODelete(p,type,n) delete[] p
#define THREAD_UNINT 0
#define kPEGraphicsMode 1
static bool gFilter=true,gTouchLat=true;
struct IOLock{};
static void IOLockLock(IOLock*){}
static void IOLockUnlock(IOLock*){}
static void IOLockWakeup(IOLock*,void*,bool){}
static void IOLockSleep(IOLock*,void*,int){assert(false);}
static void PE_initialize_console(void*,int){}
static void redwood_tl(const char*,...){}
static uint32_t refreshHz(){return 120;}
static uint64_t mach_absolute_time(){return 0;}
static uint64_t micros(uint64_t t){return t;}
struct MiniIOSurface {
 struct {uint32_t width,height,pixelFormat,planeCount=0,bytesPerElement=4,bytesPerRow;uint64_t offset=0,allocSize;} info;
 uint32_t sid=123;std::vector<uint8_t> data;
 MiniIOSurface(unsigned w,unsigned h,uint32_t format){info.width=w;info.height=h;info.pixelFormat=format;info.bytesPerRow=(w*4+63)&~63;info.allocSize=uint64_t(info.bytesPerRow)*h;data.resize(info.allocSize);}
 uint8_t *pixels(){return data.data();}
 uint32_t& at(unsigned x,unsigned y){return ((uint32_t*)(pixels()+size_t(y)*info.bytesPerRow))[x];}
};
static MiniIOSurface* MiniIOSurfaceCreateSimple(unsigned w,unsigned h,uint32_t f){return new MiniIOSurface(w,h,f);}
static unsigned presentations;
static int lemonade_display_direct_present(void*,uint64_t,unsigned,unsigned,unsigned){presentations++;return 1;}
struct RowJob;
class IOMobileFramebuffer {
public:
 uint32_t fWidth=1125,fHeight=2436,fPanelWidth=1080,fPanelHeight=2400,fRowBytes=1080*4;
 std::vector<uint8_t> panel=std::vector<uint8_t>(size_t(fRowBytes)*fPanelHeight);
 uint8_t* fBase=panel.data(); uint32_t columns[1125]={};uint16_t weights[1125]={};uint32_t *fColumn=columns;uint16_t *fColumnWeight=weights;
 uint32_t *fBlendRow=nullptr,*fHelperBlend[7]={};uint32_t fBlendRowSize=0;
 bool fDiff=false,fGraphics=false; uint64_t fDiffPixels=0;uint32_t fDiffLeft=0,fDiffRight=0,fDiffTop=0,fDiffBottom=0;
 unsigned fThreads=1,fHelpersStarted=0,fHelpersBusy=0;uint64_t fHelperGeneration=0;IOLock *fHelperLock=nullptr;const RowJob *fHelperJob=nullptr;
 MiniIOSurface *fCompositeSurface=nullptr,*fCompositeSpare=nullptr;bool fScanoutStarted=false;
 ~IOMobileFramebuffer(){delete[] fBlendRow;delete fCompositeSurface;delete fCompositeSpare;}
 std::function<bool(MiniIOSurface*,uint32_t)> queueHook;
 bool queueComposite(MiniIOSurface *s,uint32_t id){assert(queueHook);return queueHook(s,id);}
 void drawRows(const RowJob*,int32_t,int32_t,uint32_t*);
 void drawLayer(MiniIOSurface*,const int32_t*,const int32_t*,MiniIOSurface* =nullptr,bool=false);
 int composite(const uint8_t*,MiniIOSurface**);
};
'''
tests=r'''
static void word(uint8_t *s,unsigned at,uint32_t v){memcpy(s+at,&v,4);}
static void rect(uint8_t *s,unsigned at,int x,int y,int w,int h){int32_t r[]={x,y,w,h};memcpy(s+at,r,16);}
static void fill(MiniIOSurface &s,uint32_t v){for(unsigned y=0;y<s.info.height;y++)for(unsigned x=0;x<s.info.width;x++)s.at(x,y)=v;}
int main(){
 std::mt19937 random(7);
 for(unsigned n=0;n<50000;n++){
   uint32_t a=random()%256,src=a<<24,dst=random(),expected=0;
   for(unsigned c=0;c<24;c+=8)src|=(random()%(a+1))<<c;
   for(unsigned c=0;c<32;c+=8){unsigned value=((src>>c)&255)+unsigned((((dst>>c)&255)*(255-a)/255.0)+0.5);expected|=(value>255?255:value)<<c;}
   assert(source_over(src,dst)==expected);
 }
 puts("PASS premultiplied source-over matches independent scalar reference for 50000 pixels");
 for(unsigned length=0;length<257;length++)for(unsigned mode=0;mode<4;mode++){
   std::vector<uint32_t> in(length),out(length+2),expected(length+2);
   for(unsigned i=0;i<length+2;i++)out[i]=expected[i]=random();
   for(unsigned i=0;i<length;i++){
     unsigned a=mode==0?0:mode==1?255:random()%256;in[i]=a<<24;
     for(unsigned c=0;c<24;c+=8)in[i]|=(random()%(a+1))<<c;
     expected[i+1]=source_over(in[i],out[i+1]);
   }
   source_over_row(out.data()+1,in.data(),length);assert(out==expected);
 }
 puts("PASS block compositor matches scalar reference for all tails, opacity modes and guarded row bounds");
 IOMobileFramebuffer f;MiniIOSurface base(1125,2436,FOURCC('B','G','R','A')),overlay(4,4,FOURCC('B','G','R','A'));fill(base,0xff204060);fill(overlay,0x80400000);
 uint8_t swap[0x46c]={};MiniIOSurface *surfaces[]={&base,&overlay,nullptr,nullptr};
 word(swap,SWAP_SURFACE(0),1);word(swap,SWAP_SURFACE(1),2);rect(swap,SWAP_SOURCE(0),0,0,1125,2436);rect(swap,SWAP_DEST(0),0,0,1125,2436);
 rect(swap,SWAP_SOURCE(1),0,0,4,4);rect(swap,SWAP_DEST(1),100,200,4,4);
 assert(f.composite(swap,surfaces)==1&&presentations==1);
 assert(f.fCompositeSurface->at(100,200)==0xff502030&&f.fCompositeSurface->at(99,200)==0xff204060);
 puts("PASS two layers retain background and blend translucent overlay at logical coordinates");
 overlay.at(0,0)=0;overlay.at(1,0)=0xff00ff00;assert(f.composite(swap,surfaces)==1);
 assert(f.fCompositeSurface->at(100,200)==0xff204060&&f.fCompositeSurface->at(101,200)==0xff00ff00);
 word(swap,SWAP_SURFACE(0),0);word(swap,SWAP_SURFACE(1),0);word(swap,SWAP_SURFACE(2),3);surfaces[2]=&overlay;
 rect(swap,SWAP_SOURCE(2),0,0,4,4);rect(swap,SWAP_DEST(2),-2,-2,8,8);
 assert(f.composite(swap,surfaces)==1&&f.fCompositeSurface->at(100,200)==0xff000000);
 assert(f.fCompositeSurface->at(5,5)==0xff400000&&f.fCompositeSurface->at(6,6)==0xff000000);
 puts("PASS overlay removal clears old pixels; absent base, scaled and clipped layers stay within bounds");
 rect(swap,SWAP_DEST(2),-20,0,4,4);assert(f.composite(swap,surfaces)==1);assert(f.fCompositeSurface->at(0,0)==0xff000000);
 rect(swap,SWAP_DEST(2),1125,0,4,4);assert(f.composite(swap,surfaces)==1);
 rect(swap,SWAP_SOURCE(2),2147483646,0,4,4);assert(f.composite(swap,surfaces)==1);
 rect(swap,SWAP_SOURCE(2),0,0,4,4);rect(swap,SWAP_DEST(2),-2147483646,-2147483646,2147483647,2147483647);
 assert(f.composite(swap,surfaces)==1&&f.fCompositeSurface->at(0,0)==0xff400000);
 puts("PASS fully offscreen and overflowing source rectangles never read or write outside their buffers");
 auto allocated=overlay.info.allocSize;overlay.info.allocSize=1;unsigned before=presentations;assert(f.composite(swap,surfaces)==0&&presentations==before);overlay.info.allocSize=allocated;
 overlay.info.pixelFormat=0;assert(f.composite(swap,surfaces)==0&&presentations==before);
 puts("PASS short allocations and unsupported formats are rejected before direct presentation");
 {
   IOMobileFramebuffer pipeline;pipeline.fScanoutStarted=true;MiniIOSurface *scanning=nullptr;
   std::vector<uint8_t> immutable;unsigned submitted=0;
   pipeline.queueHook=[&](MiniIOSurface *next,uint32_t){
     if(scanning){assert(next!=scanning&&scanning->data==immutable);}
     scanning=next;immutable=next->data;submitted++;return true;
   };
   uint8_t frame[0x46c]={};MiniIOSurface *layers[]={&base,nullptr,nullptr,nullptr};
   word(frame,SWAP_SURFACE(0),1);rect(frame,SWAP_SOURCE(0),0,0,1125,2436);rect(frame,SWAP_DEST(0),0,0,1125,2436);
   for(unsigned n=0;n<5;n++){
     fill(base,0xff000000|n);assert(pipeline.composite(frame,layers)==1);
     assert(pipeline.fCompositeSurface->at(0,0)==(0xff000000|n));
   }
   assert(submitted==5&&pipeline.fCompositeSpare&&pipeline.fCompositeSurface!=pipeline.fCompositeSpare);
 }
 puts("PASS real compositor alternates owned buffers without modifying the frame still being scanned");
 // Preserve the ordinary firmware path's output when no owned target is supplied.
 fill(base,0xff204060);
 int32_t src[]={0,0,1125,2436},dst[]={0,0,1125,2436};f.drawLayer(&base,src,dst);
 assert(((uint32_t*)f.panel.data())[0]==0xff204060);
 puts("PASS legacy scaler continues to address physical framebuffer dimensions");
}
'''
code=pre+inline+row+'\n'+fences.method(s,'swapWord')+'\n'+'\n'.join(fences.method(s,n) for n in ('IOMobileFramebuffer::drawRows','IOMobileFramebuffer::drawLayer','IOMobileFramebuffer::composite'))+tests
with tempfile.TemporaryDirectory(prefix='lemonade-composite-') as temp:
 p=Path(temp);(p/'test.cpp').write_text(code)
 subprocess.run(['clang++','-std=c++17','-fsanitize=address,undefined',str(p/'test.cpp'),'-o',str(p/'test')],check=True)
 subprocess.run([str(p/'test')],check=True)
