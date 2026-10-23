/* Public MPS acceptance; identical source runs on Apple and Adreno Metal. */
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <MetalPerformanceShaders/MetalPerformanceShaders.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <signal.h>
#include <sys/ucontext.h>
#include <dlfcn.h>
#include <unistd.h>

/* Diagnostic process only: preserve image-relative locations before an abort.
 * No recovery is attempted after a fault. */
static void fault(int number, siginfo_t *info, void *opaque) {
    ucontext_t *context=opaque;
    uintptr_t pc=context->uc_mcontext->__ss.__pc, fp=context->uc_mcontext->__ss.__fp;
    dprintf(2,"lemonade-metal: MPS signal=%d address=%p\n",number,info->si_addr);
    for(unsigned i=0;i<12 && pc;i++) {
        Dl_info symbol={0};
        if(dladdr((void *)pc,&symbol)) dprintf(2,"lemonade-metal: MPS frame=%u image=%s offset=%#lx symbol=%s\n",i,
            symbol.dli_fname,(unsigned long)(pc-(uintptr_t)symbol.dli_fbase),symbol.dli_sname?symbol.dli_sname:"?");
        if(!fp || fp%8) break;
        uintptr_t next=((uintptr_t *)fp)[0];pc=((uintptr_t *)fp)[1];fp=next>fp?next:0;
    }
    _exit(128+number);
}

int main(void) {
    @autoreleasepool {
        setenv("REDWOOD_METAL_TRACE", "1", 1);
        struct sigaction action={0}; action.sa_sigaction=fault; action.sa_flags=SA_SIGINFO;
        sigaction(SIGSEGV,&action,NULL);sigaction(SIGBUS,&action,NULL);sigaction(SIGABRT,&action,NULL);
        id<MTLDevice> device = MTLCreateSystemDefaultDevice();
        if (!device) { printf("lemonade-metal: MPS setup FAIL no default device\n"); return 1; }
        printf("lemonade-metal: MPS device %s\n", device.name.UTF8String);
        id<MTLCommandQueue> queue = [device newCommandQueue];
        MTLTextureDescriptor *td = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm width:17 height:17 mipmapped:NO];
        td.usage = MTLTextureUsageShaderRead | MTLTextureUsageShaderWrite;
        id<MTLTexture> source = [device newTextureWithDescriptor:td];
        id<MTLTexture> target = [device newTextureWithDescriptor:td];
        if (!queue || !source || !target) { printf("lemonade-metal: MPS setup FAIL resource allocation\n"); return 1; }
        MPSImageGaussianBlur *blur = [[MPSImageGaussianBlur alloc] initWithDevice:device sigma:2.0];
        blur.edgeMode = MPSImageEdgeModeClamp;
        unsigned failures = 0;
        for (unsigned test=0; test<2; test++) {
            uint8_t input[17*17*4], output[sizeof(input)];
            memset(input, 0, sizeof(input)); memset(output, 0xa5, sizeof(output));
            if (!test) {
                for (unsigned i=0;i<17*17;i++) { input[i*4]=63; input[i*4+1]=127; input[i*4+2]=191; input[i*4+3]=255; }
            } else { input[(8*17+8)*4]=255; input[(8*17+8)*4+1]=128; input[(8*17+8)*4+2]=64; input[(8*17+8)*4+3]=255; }
            [source replaceRegion:MTLRegionMake2D(0,0,17,17) mipmapLevel:0 withBytes:input bytesPerRow:68];
            id<MTLCommandBuffer> command = [queue commandBuffer];
            printf("lemonade-metal: MPS command device_same=%d queue_same=%d apple2=%d conforms=%d dictionary=%s\n",
                command.device==device, command.commandQueue==queue, [command.device supportsFamily:MTLGPUFamilyApple2],
                [command conformsToProtocol:@protocol(MTLCommandBuffer)],
                [(id)command respondsToSelector:NSSelectorFromString(@"userDictionary")] ?
                    NSStringFromClass([[(id)command performSelector:NSSelectorFromString(@"userDictionary")] class]).UTF8String : "unavailable");
            [blur encodeToCommandBuffer:command sourceTexture:source destinationTexture:target];
            [command commit]; [command waitUntilCompleted];
            [target getBytes:output bytesPerRow:68 fromRegion:MTLRegionMake2D(0,0,17,17) mipmapLevel:0];
            unsigned different=0, energy=0;
            if (!test) for (unsigned i=0;i<sizeof(input);i++) different += abs((int)input[i]-output[i])>1;
            else {
                for (unsigned y=0;y<17;y++) for (unsigned x=0;x<17;x++) {
                    energy+=output[(y*17+x)*4];
                    for (unsigned c=0;c<4;c++) {
                        different+=abs((int)output[(y*17+x)*4+c]-output[(y*17+16-x)*4+c])>1;
                        different+=abs((int)output[(y*17+x)*4+c]-output[((16-y)*17+x)*4+c])>1;
                    }
                }
                different += energy<210 || energy>280;
                different += output[(8*17+8)*4] <= output[(8*17+11)*4];
                different += output[(8*17+11)*4] <= output[(8*17+15)*4];
            }
            BOOL ok=command.status==MTLCommandBufferStatusCompleted && !different;
            failures+=!ok;
            printf("lemonade-metal: MPS gaussian-%s %s status=%lu mismatches=%u energy=%u\n", test ? "impulse" : "constant", ok?"PASS":"FAIL", (unsigned long)command.status, different,energy);
            if(command.error) printf("lemonade-metal: MPS error %s\n",command.error.localizedDescription.UTF8String);
        }
        [blur release]; [source release]; [target release]; [queue release];
        printf("lemonade-metal: MPS summary checks=2 failed=%u\n",failures);
        return failures?1:0;
    }
}
