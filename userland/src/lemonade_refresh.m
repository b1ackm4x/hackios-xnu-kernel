/* UIKit frame-clock acceptance. Callback counts alone are not panel FPS:
 * correlate the timed phases below with held IOSurface / DPU completion logs. */
#import <UIKit/UIKit.h>
#import <QuartzCore/QuartzCore.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdio.h>
#include <unistd.h>

static void report(const char *format, ...)
{
    char text[512]; va_list ap; va_start(ap, format);
    int n = vsnprintf(text, sizeof(text), format, ap); va_end(ap);
    int fd = open("/dev/console", O_WRONLY);
    if (fd >= 0) { write(fd, text, n < (int)sizeof(text) ? n : sizeof(text)-1); close(fd); }
}

@interface RefreshDelegate : UIResponder <UIApplicationDelegate>
@property(nonatomic, strong) UIWindow *window;
@property(nonatomic, strong) UIView *bar;
@property(nonatomic, strong) UILabel *label;
@property(nonatomic, strong) NSArray<CALayer *> *bits;
@property(nonatomic, strong) CADisplayLink *link;
@end

@implementation RefreshDelegate {
    double _start, _last, _interval, _min, _max, _reportAt;
    unsigned _count, _samples, _late, _phase;
}
- (BOOL)application:(UIApplication *)application didFinishLaunchingWithOptions:(NSDictionary *)options
{
    (void)application; (void)options;
    self.window = [[UIWindow alloc] initWithFrame:UIScreen.mainScreen.bounds];
    UIViewController *controller = [UIViewController new];
    self.window.rootViewController = controller;
    controller.view.backgroundColor = [UIColor colorWithWhite:0.08 alpha:1];
    CGFloat width = self.window.bounds.size.width;
    self.label = [[UILabel alloc] initWithFrame:CGRectMake(20, 90, width-40, 100)];
    self.label.textColor = UIColor.whiteColor;
    self.label.font = [UIFont monospacedDigitSystemFontOfSize:21 weight:UIFontWeightMedium];
    self.label.numberOfLines = 3;
    self.label.text = @"Lemonade refresh\nUIKit / Core Animation";
    [controller.view addSubview:self.label];
    self.bar = [[UIView alloc] initWithFrame:CGRectMake(0, 240, 40, 240)];
    self.bar.backgroundColor = [UIColor colorWithRed:0.1 green:0.8 blue:0.65 alpha:1];
    [controller.view addSubview:self.bar];
    NSMutableArray *bits = [NSMutableArray new];
    for (unsigned i=0; i<12; i++) {
        CALayer *bit = [CALayer layer];
        bit.frame = CGRectMake(20+i*(width-40)/12, 550, (width-40)/12-2, 35);
        bit.backgroundColor = UIColor.blackColor.CGColor;
        [controller.view.layer addSublayer:bit]; [bits addObject:bit];
    }
    self.bits = bits;
    [self.window makeKeyAndVisible];
    self.link = [CADisplayLink displayLinkWithTarget:self selector:@selector(tick:)];
    self.link.preferredFrameRateRange = CAFrameRateRangeMake(120, 120, 120);
    [self.link addToRunLoop:NSRunLoop.mainRunLoop forMode:NSRunLoopCommonModes];
    report("refresh-app: start max_fps=%ld preferred=%.0f/%.0f/%.0f scale=%.1f\n",
        (long)UIScreen.mainScreen.maximumFramesPerSecond, self.link.preferredFrameRateRange.minimum,
        self.link.preferredFrameRateRange.maximum, self.link.preferredFrameRateRange.preferred,
        UIScreen.mainScreen.scale);
    return YES;
}
- (void)tick:(CADisplayLink *)link
{
    double now = CACurrentMediaTime();
    if (!_start) { _start = _reportAt = now; _min = 1; report("refresh-app: phase=displaylink at_us=%.0f\n", now*1e6); }
    double elapsed = now-_start;
    if (_last) {
        double dt=now-_last; _interval+=dt; _samples++;
        if(dt<_min)_min=dt; if(dt>_max)_max=dt; if(dt>0.0125)_late++;
    }
    _last=now; _count++;
    if (!_phase) {
        [CATransaction begin]; [CATransaction setDisableActions:YES];
        double p=fmod(elapsed,2.0)/2.0;
        self.bar.layer.position=CGPointMake(20+(self.window.bounds.size.width-40)*p,360);
        for(unsigned i=0;i<12;i++) self.bits[i].backgroundColor = ((_count>>i)&1) ? UIColor.whiteColor.CGColor : UIColor.blackColor.CGColor;
        [CATransaction commit];
    }
    if (elapsed>=20 && !_phase) {
        _phase=1;
        CABasicAnimation *animation=[CABasicAnimation animationWithKeyPath:@"position.x"];
        animation.fromValue=@20; animation.toValue=@(self.window.bounds.size.width-20);
        animation.duration=1; animation.autoreverses=YES; animation.repeatCount=HUGE_VALF;
        animation.preferredFrameRateRange=CAFrameRateRangeMake(120,120,120);
        [self.bar.layer addAnimation:animation forKey:@"sweep"];
        report("refresh-app: phase=coreanimation at_us=%.0f count=%u\n",now*1e6,_count);
    }
    if(now-_reportAt>=5) {
        report("refresh-app: phase=%u at_us=%.0f frames=%u samples=%u fps=%.3f min_us=%.0f max_us=%.0f late=%u duration_us=%.0f target_us=%.0f max_fps=%ld\n",
            _phase,now*1e6,_count,_samples,_samples/_interval,_min*1e6,_max*1e6,_late,
            link.duration*1e6,(link.targetTimestamp-link.timestamp)*1e6,(long)UIScreen.mainScreen.maximumFramesPerSecond);
        self.label.text=[NSString stringWithFormat:@"Lemonade refresh\n%.1f callbacks / s\n%@",_samples/_interval,_phase?@"Core Animation":@"CADisplayLink"];
        _reportAt=now; _interval=0; _samples=0; _min=1; _max=0; _late=0;
    }
}
@end

int main(int argc,char **argv)
{
    @autoreleasepool { return UIApplicationMain(argc,argv,nil,NSStringFromClass(RefreshDelegate.class)); }
}
