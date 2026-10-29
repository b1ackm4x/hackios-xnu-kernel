/* Delta-only diagnostics and the existing Redwood OpenGL ES implementation. */
#import <Foundation/Foundation.h>
#import <OpenGLES/EAGL.h>
#include <dlfcn.h>
#include <execinfo.h>
#include <fcntl.h>
#include <mach-o/dyld.h>
#include <signal.h>
#include <unistd.h>
#include <stdio.h>
static int console=-1;
static void frames(void){void *stack[40];int count=backtrace(stack,40);backtrace_symbols_fd(stack,count,console>=0?console:2);}
static void fault(int number,siginfo_t *info,void *context){
    (void)context;char line[160];int n=snprintf(line,sizeof(line),"delta-crash: signal=%d address=%p\n",number,info?info->si_addr:NULL);
    if(n>0)(void)write(console>=0?console:2,line,(size_t)n);frames();signal(number,SIG_DFL);raise(number);
}
static NSUncaughtExceptionHandler *previous;
static void exception(NSException *value){
    fprintf(stderr,"delta-crash: exception=%s reason=%s\n",value.name.UTF8String,value.reason.UTF8String?:"none");frames();if(previous)previous(value);
}
__attribute__((constructor)) static void begin(void){
    if(strcmp(getprogname(),"Delta"))return;
    console=open("/dev/console",O_WRONLY);if(console>=0){dup2(console,1);dup2(console,2);}
    setvbuf(stdout,NULL,_IOLBF,0);setvbuf(stderr,NULL,_IOLBF,0);
    fprintf(stderr,"delta-crash: logger_loaded uid=%u\n",getuid());
    const int watched[]={SIGABRT,SIGSEGV,SIGBUS,SIGILL,SIGTRAP,SIGSYS};
    struct sigaction action={0};action.sa_sigaction=fault;action.sa_flags=SA_SIGINFO|SA_NODEFER|SA_RESETHAND;sigemptyset(&action.sa_mask);
    for(unsigned i=0;i<sizeof(watched)/sizeof(watched[0]);i++)sigaction(watched[i],&action,NULL);
    previous=NSGetUncaughtExceptionHandler();NSSetUncaughtExceptionHandler(exception);
    @autoreleasepool {
        EAGLContext *before=[[EAGLContext alloc] initWithAPI:kEAGLRenderingAPIOpenGLES2];
        fprintf(stderr,"delta-gl: native_es2_context=%d\n",before!=nil);
        if (!before) {
            void *library=dlopen("/redwood/lib/libredwood-gl.dylib",RTLD_NOW|RTLD_LOCAL);
            fprintf(stderr,"delta-gl: redwood_library_loaded=%d\n",library!=NULL);
            if (library) {
                EAGLContext *after=[[EAGLContext alloc] initWithAPI:kEAGLRenderingAPIOpenGLES2];
                fprintf(stderr,"delta-gl: redwood_es2_context=%d api=%lu\n",after!=nil,(unsigned long)after.API);
            }
        }
    }
}
