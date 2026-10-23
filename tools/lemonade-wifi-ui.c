/* Open the real iOS Wi-Fi Settings page for framebuffer capture in a RAM test. */
#include <CoreFoundation/CoreFoundation.h>
#include <dlfcn.h>
#include <stdio.h>
#include <spawn.h>
#include <string.h>
#include <sys/sysctl.h>
#include <sys/wait.h>
#include <unistd.h>

extern char **environ;
static int touch(char *const steps[]) {
    pid_t pid; int status = 0;
    int error = posix_spawn(&pid, steps[0], NULL, NULL, steps, environ);
    if (error || waitpid(pid, &status, 0) != pid) return 1;
    return !WIFEXITED(status) || WEXITSTATUS(status);
}

int main(int argc, char **argv) {
    int details = argc == 2 && !strcmp(argv[1], "details");
    int browser = argc == 2 && !strcmp(argv[1], "bilibili");
    if (argc > 1 && !details && !browser) return 2;
    char args[2048] = {0}; size_t size = sizeof(args);
    if (sysctlbyname("kern.bootargs", args, &size, NULL, 0)) return 1;
    args[sizeof(args) - 1] = 0;
    int enabled = 0;
    for (char *word = strtok(args, " "); word; word = strtok(NULL, " "))
        if (!strcmp(word, "lemonade_wifi=24")) enabled = 1;
    if (!enabled) return 0;
    setvbuf(stdout, NULL, _IOLBF, 0);
    sleep(90);
    void *library = dlopen("/System/Library/PrivateFrameworks/SpringBoardServices.framework/SpringBoardServices", RTLD_NOW);
    int (*open_url)(CFURLRef, Boolean) = library ? dlsym(library, "SBSOpenSensitiveURLAndUnlock") : NULL;
    CFURLRef url = CFURLCreateWithString(NULL, browser ? CFSTR("https://m.bilibili.com/") : CFSTR("prefs:root=WIFI"), NULL);
    if (!open_url || !url) { puts("lemonade-ui: Settings opener unavailable"); return 1; }
    int result = open_url(url, true);
    printf("lemonade-ui: requested %s; API result %d\n", browser ? "Bilibili in the system browser" : "real Wi-Fi Settings page", result);
    CFRelease(url);
    if (details) {
        /* Positions observed in the actual 1080x2400 Wi-Fi framebuffer. */
        sleep(100);
        char *tap[] = { "/redwood/bin/rwtouch", "tap", "0.882", "0.235", NULL };
        printf("lemonade-ui: open current network information; input result %d\n", touch(tap));
        sleep(55);
        char *scroll[] = { "/redwood/bin/rwtouch", "swipe", "0.5", "0.85", "0.5", "0.43", "500", NULL };
        printf("lemonade-ui: scroll network information; input result %d\n", touch(scroll));
    }
    return 0;
}
