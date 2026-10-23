#!/usr/bin/env python3
"""Run the frontend's actual ownership methods against deallocation sentinels.

Uses Foundation objects, no GPU or private Metal superclass. The production
encoder ivars, selected methods and recorded-work ownership are compiled intact;
only the superclass, device, attachments and resource objects are host substitutes.
Optional source path allows the same test to demonstrate the previous failure.
"""
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def mask(text):
    return re.sub(r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'',
                  lambda match: ' ' * len(match[0]), text, flags=re.S)


def balanced(text, start):
    masked = mask(text)
    depth = 0
    for index in range(start, len(text)):
        if masked[index] == '{':
            depth += 1
        elif masked[index] == '}':
            depth -= 1
            if not depth:
                return index + 1
    raise ValueError('Unclosed method or ivar block')


def implementation(source, name):
    begin = source.index('@implementation ' + name)
    return source[begin:source.index('\n@end', begin) + 5]


def methods(block, names):
    selected = []
    for match in re.finditer(r'^-\s*\([^\n)]*\)\s*(\w+)', block, re.M):
        if match[1] in names:
            body = block.index('{', match.end())
            selected.append(block[match.start():balanced(block, body)])
    return '\n'.join(selected)


PRELUDE = r'''
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <objc/runtime.h>
#include <assert.h>
#include <stdio.h>
#include <string.h>
static unsigned deaths[256], nextBytes=128;
static void rw_print(const char *format, ...) {}
#define RW_ONCE(...) rw_print(__VA_ARGS__)
@interface ProbeResource : NSObject { unsigned _tag; }
- (instancetype)initWithTag:(unsigned)tag;
@end
@implementation ProbeResource
- (instancetype)initWithTag:(unsigned)tag { if((self=[super init]))_tag=tag;return self; }
- (void)dealloc { deaths[_tag]++;[super dealloc]; }
@end
@interface RedwoodMetalRenderPipeline : ProbeResource
- (BOOL)writesDepth;
@end
@implementation RedwoodMetalRenderPipeline
- (BOOL)writesDepth {return NO;}
@end
@interface RedwoodMetalComputePipeline : ProbeResource @end
@implementation RedwoodMetalComputePipeline @end
@interface RedwoodMetalDepthStencilState : ProbeResource @end
@implementation RedwoodMetalDepthStencilState @end
@interface HostDevice : NSObject
- (id)newBufferWithBytes:(const void *)bytes length:(NSUInteger)size options:(NSUInteger)options;
@end
@implementation HostDevice
- (id)newBufferWithBytes:(const void *)bytes length:(NSUInteger)size options:(NSUInteger)options {
    return [[ProbeResource alloc] initWithTag:nextBytes++];
}
@end
@interface HostAttachment : NSObject
@property(retain) id texture,resolveTexture;
@property NSUInteger loadAction,storeAction,level,slice,depthPlane;
@property NSUInteger resolveLevel,resolveSlice,resolveDepthPlane,storeActionOptions;
@property MTLClearColor clearColor;
@property double clearDepth;
@property unsigned clearStencil;
@end
@implementation HostAttachment
- (void)dealloc { [_texture release];[_resolveTexture release];[super dealloc]; }
@end
@interface HostPass : NSObject
@property(retain) NSArray *colorAttachments;
@property(retain) HostAttachment *depthAttachment,*stencilAttachment;
@end
@implementation HostPass
- (instancetype)init {
    if((self=[super init])) {
        NSMutableArray *colors=[[NSMutableArray alloc]init];
        for(unsigned i=0;i<8;i++){HostAttachment *a=[[HostAttachment alloc]init];[colors addObject:a];[a release];}
        _colorAttachments=colors;_depthAttachment=[[HostAttachment alloc]init];_stencilAttachment=[[HostAttachment alloc]init];
    }
    return self;
}
- (void)dealloc {[_colorAttachments release];[_depthAttachment release];[_stencilAttachment release];[super dealloc];}
@end
@class RedwoodMetalWork;
@interface RedwoodMetalCommandBuffer : NSObject {
    NSMutableArray *_work;
    HostDevice *_device;
}
- (void)recordWork:(RedwoodMetalWork *)work;
- (id)device;
@end
@interface HostEncoder : NSObject { id _heldCommand; }
- (instancetype)initWithCommandBuffer:(id)command;
- (void)endEncoding;
@end
@implementation HostEncoder
- (instancetype)initWithCommandBuffer:(id)command {if((self=[super init]))_heldCommand=[command retain];return self;}
- (void)endEncoding {[_heldCommand release];_heldCommand=nil;}
- (void)dealloc {[_heldCommand release];[super dealloc];}
@end
'''

COMMAND_BUFFER = r'''
- (instancetype)init {if((self=[super init])){_work=[[NSMutableArray alloc]init];_device=[[HostDevice alloc]init];}return self;}
- (id)device {return _device;}
- (void)dealloc {[_work release];[_device release];[super dealloc];}
'''

CASES = r'''
static ProbeResource *resource(unsigned tag) {return [[ProbeResource alloc]initWithTag:tag];}
static HostPass *passWithResources(unsigned first) {
    HostPass *pass=[[HostPass alloc]init];
    HostAttachment *color=pass.colorAttachments[0];
    color.texture=[resource(first) autorelease];
    pass.depthAttachment.texture=[resource(first+1) autorelease];
    return pass;
}
static void allAlive(unsigned first,unsigned count) {for(unsigned i=first;i<first+count;i++)assert(deaths[i]==0);}
static void allDead(unsigned first,unsigned count) {for(unsigned i=first;i<first+count;i++)assert(deaths[i]==1);}
int main(void) {
    @autoreleasepool {
        RedwoodMetalCommandBuffer *command=[[RedwoodMetalCommandBuffer alloc]init];
        RedwoodMetalRenderEncoder *encoder;
        @autoreleasepool {
            HostPass *pass=passWithResources(0);
            encoder=[[RedwoodMetalRenderEncoder alloc]initWithRedwoodCommandBuffer:command descriptor:(id)pass];
            [pass release];
        }
        allAlive(0,2);
        id pipeline=[[RedwoodMetalRenderPipeline alloc]initWithTag:2];
        id vertex=resource(3),fragment=resource(4),texture=resource(5),sampler=resource(6);
        id depthState=[[RedwoodMetalDepthStencilState alloc]initWithTag:7];
        [encoder setRenderPipelineState:pipeline];[pipeline release];
        [encoder setVertexBuffer:vertex offset:0 atIndex:0];[encoder setVertexBuffer:vertex offset:16 atIndex:0];[vertex release];
        [encoder setFragmentBuffer:fragment offset:0 atIndex:0];[fragment release];
        [encoder setFragmentTexture:texture atIndex:0];[texture release];
        [encoder setFragmentSamplerState:sampler atIndex:0];[sampler release];
        [encoder setDepthStencilState:depthState];[depthState release];
        allAlive(0,8);
        [encoder recordDraw:3 vertexStart:0 count:3 indexBuffer:nil indexType:0 indexOffset:0 baseVertex:0 instanceCount:1 baseInstance:0];
        id replacement=resource(8);[encoder setVertexBuffer:replacement offset:0 atIndex:0];[replacement release];
        [encoder setFragmentTexture:nil atIndex:0];allAlive(0,9);
        [encoder endEncoding];[encoder endEncoding];[encoder release];
        allAlive(0,8);allDead(8,1);
        [command release];allDead(0,9);
        puts("PASS: render bindings and attachments survive early release; recorded draws survive rebinding and encoder destruction");
    }
    @autoreleasepool {
        RedwoodMetalCommandBuffer *command=[[RedwoodMetalCommandBuffer alloc]init];
        RedwoodMetalRenderEncoder *encoder;
        @autoreleasepool {
            HostPass *pass=passWithResources(10);
            encoder=[[RedwoodMetalRenderEncoder alloc]initWithRedwoodCommandBuffer:command descriptor:(id)pass];[pass release];
        }
        id first=resource(12),second=resource(13);
        [encoder setFragmentBuffer:first offset:0 atIndex:0];[first release];
        [encoder setFragmentBuffer:second offset:0 atIndex:0];[second release];
        allDead(12,1);allAlive(13,1);[encoder setFragmentBuffer:nil offset:0 atIndex:0];allDead(13,1);
        [encoder endEncoding];[encoder release];[command release];allDead(10,4);
        puts("PASS: unused and replaced bindings are released without waiting for a draw");
    }
    @autoreleasepool {
        RedwoodMetalCommandBuffer *command=[[RedwoodMetalCommandBuffer alloc]init];
        RedwoodMetalComputeEncoder *encoder=[[RedwoodMetalComputeEncoder alloc]initWithRedwoodCommandBuffer:command];
        id pipeline=[[RedwoodMetalComputePipeline alloc]initWithTag:16];
        id buffer=resource(17),texture=resource(18),sampler=resource(19);
        [encoder setComputePipelineState:pipeline];[pipeline release];
        [encoder setBuffer:buffer offset:0 atIndex:0];[encoder setBufferOffset:16 atIndex:0];[buffer release];
        [encoder setTexture:texture atIndex:0];[texture release];
        [encoder setSamplerState:sampler atIndex:0];[sampler release];allAlive(16,4);
        [encoder dispatchThreadgroups:MTLSizeMake(1,1,1) threadsPerThreadgroup:MTLSizeMake(1,1,1)];
        [encoder setBuffer:nil offset:0 atIndex:0];
        id unused=resource(20);[encoder setTexture:unused atIndex:0];[unused release];
        [encoder endEncoding];[encoder endEncoding];[encoder release];allAlive(16,4);allDead(20,1);
        [command release];allDead(16,5);
        puts("PASS: compute bindings survive early release and dispatch keeps its original resource snapshot");
    }
    @autoreleasepool {
        RedwoodMetalCommandBuffer *command=[[RedwoodMetalCommandBuffer alloc]init];
        RedwoodMetalComputeEncoder *encoder=[[RedwoodMetalComputeEncoder alloc]initWithRedwoodCommandBuffer:command];
        id pipeline=[[RedwoodMetalComputePipeline alloc]initWithTag:24];[encoder setComputePipelineState:pipeline];[pipeline release];
        unsigned first=nextBytes;
        @autoreleasepool {uint32_t value=42;[encoder setBytes:&value length:4 atIndex:0];}
        allAlive(first,1);
        [encoder dispatchThreadgroups:MTLSizeMake(1,1,1) threadsPerThreadgroup:MTLSizeMake(1,1,1)];
        [encoder endEncoding];[encoder release];allAlive(first,1);[command release];allDead(first,1);allDead(24,1);
        puts("PASS: compute setBytes survives the caller's autorelease pool until the command releases it");
    }
    @autoreleasepool {
        RedwoodMetalCommandBuffer *command=[[RedwoodMetalCommandBuffer alloc]init];
        HostPass *pass=[[HostPass alloc]init];
        RedwoodMetalRenderEncoder *encoder=[[RedwoodMetalRenderEncoder alloc]initWithRedwoodCommandBuffer:command descriptor:(id)pass];[pass release];
        unsigned first=nextBytes;
        @autoreleasepool {uint32_t value=42;[encoder setVertexBytes:&value length:4 atIndex:0];[encoder setFragmentBytes:&value length:4 atIndex:0];}
        allAlive(first,2);[encoder endEncoding];[encoder release];[command release];allDead(first,2);
        puts("PASS: render setBytes survives a pool drain and is released when unused encoding ends");
    }
    return 0;
}
'''


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / 'userland/src/redwoodmetal.m'
    source = path.read_text()
    constant = re.search(r'^#define RW_MAX_BUFFERS\s+[^\n]+', source, re.M)[0]
    begin = source.index('@interface RedwoodMetalWork :')
    work = source[begin:source.index('\n@end', begin) + 5]
    helper = ''
    if 'rw_replace_binding(id *slot' in source:
        begin = source.rindex('static void', 0, source.index('rw_replace_binding(id *slot'))
        helper = source[begin:balanced(source, source.index('{', begin))]
    text = PRELUDE + '\n' + constant + '\n' + work + '\n' + helper
    text += implementation(source, 'RedwoodMetalWork')
    text += '\n@implementation RedwoodMetalCommandBuffer\n' + COMMAND_BUFFER
    text += methods(implementation(source, 'RedwoodMetalCommandBuffer'), {'recordWork'}) + '\n@end\n'
    common = {'initWithRedwoodCommandBuffer', 'device', 'releaseEncodingResources', 'endEncoding', 'dealloc'}
    selections = {
        'RedwoodMetalRenderEncoder': common | {'setRenderPipelineState', 'setDepthStencilState',
            'setVertexBuffer', 'setFragmentBuffer', 'setVertexBytes', 'setFragmentBytes',
            'setFragmentTexture', 'setFragmentSamplerState', 'recordDraw'},
        'RedwoodMetalComputeEncoder': common | {'setComputePipelineState', 'setBuffer', 'setBufferOffset',
            'setBytes', 'setTexture', 'setSamplerState', 'dispatchThreadgroups'},
    }
    for name, selected in selections.items():
        block = implementation(source, name)
        start = block.index('{')
        ivars = block[start:balanced(block, start)]
        text += '\n@interface ' + name + ' : HostEncoder\n@end\n'
        text += '@implementation ' + name + ivars + '\n' + methods(block, selected) + '\n@end\n'
    text += CASES
    with tempfile.TemporaryDirectory(prefix='lemonade-metal-lifetime-') as directory:
        folder = Path(directory)
        (folder / 'test.m').write_text(text)
        subprocess.run(['xcrun', 'clang', '-fno-objc-arc', '-O1', '-g', '-fsanitize=address,undefined',
                        '-Wno-objc-method-access', '-framework', 'Foundation', '-framework', 'Metal',
                        str(folder / 'test.m'), '-o', str(folder / 'test')], check=True)
        subprocess.run([str(folder / 'test')], check=True)


if __name__ == '__main__':
    main()
