/* macOS-only, bounded BLE discovery fixture. Build with:
 * clang -fobjc-arc -Wall -Wextra -Werror -framework Foundation -framework CoreBluetooth \
 *   devices/oneplus-9/tools/lemonade-bt-discovery-target.m -o /tmp/lemonade-bt-discovery-target
 * Run while the exact phone executes lemonade-validate-base-bluetooth.py.
 * Advertises a recognizable test label for ten minutes, without GATT services,
 * keyboard reports, connection requests or pairing. SIGINT/SIGTERM stops it.
 */
#import <Foundation/Foundation.h>
#import <CoreBluetooth/CoreBluetooth.h>
#include <signal.h>
#include <stdio.h>
static volatile sig_atomic_t stopped=0;
static void stop(int number){(void)number;stopped=1;}
@interface DiscoveryTarget:NSObject<CBPeripheralManagerDelegate>
@property(nonatomic,strong) CBPeripheralManager *manager;
@property(nonatomic) BOOL failed;
@end
@implementation DiscoveryTarget
-(void)peripheralManagerDidUpdateState:(CBPeripheralManager*)manager{
 printf("target: state=%ld\n",(long)manager.state);fflush(stdout);
 if(manager.state==CBManagerStatePoweredOn){
  // Discovery-only test fixture. No keyboard data, connections or pairing.
  [manager startAdvertising:@{CBAdvertisementDataLocalNameKey:@"Lemonade Test",CBAdvertisementDataServiceUUIDsKey:@[[CBUUID UUIDWithString:@"1812"]]}];
 }else if(manager.state==CBManagerStateUnauthorized||manager.state==CBManagerStateUnsupported){self.failed=YES;}
}
-(void)peripheralManagerDidStartAdvertising:(CBPeripheralManager*)manager error:(NSError*)error{
 printf("target: advertising=%d error=%ld\n",manager.isAdvertising,(long)error.code);fflush(stdout);
 if(error)self.failed=YES;
}
@end
int main(void){@autoreleasepool{
 signal(SIGINT,stop);signal(SIGTERM,stop);
 DiscoveryTarget *target=[DiscoveryTarget new];
 target.manager=[[CBPeripheralManager alloc]initWithDelegate:target queue:nil options:@{CBPeripheralManagerOptionShowPowerAlertKey:@NO}];
 NSDate *deadline=[NSDate dateWithTimeIntervalSinceNow:600];
 while(!stopped&&!target.failed&&deadline.timeIntervalSinceNow>0){[[NSRunLoop mainRunLoop]runUntilDate:[NSDate dateWithTimeIntervalSinceNow:0.2]];}
 [target.manager stopAdvertising];printf("target: stopped\n");return target.failed?1:0;
}}
