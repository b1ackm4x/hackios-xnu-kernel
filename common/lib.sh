# Shared by common/*.sh: find the workspace and load a device profile.
#   . "$(dirname "$(readlink -f "$0")")/lib.sh"; load_device "$1"
ROOT=$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)
cd "$ROOT" || exit 1

die() { echo "${0##*/}: $*" >&2; exit 1; }

# load_device accepts upstream directory names and existing runtime codenames.
load_device() {
    local name=${1:-${DEVICE:-}}
    [ -n "$name" ] || die "which device? oneplus-9 poco-x5-pro qemu-virt"
    case "$name" in
        oneplus-9|lemonade) DEVICE=lemonade; DEVICE_DIR=devices/oneplus-9 ;;
        poco-x5-pro|redwood) DEVICE=redwood; DEVICE_DIR=devices/poco-x5-pro ;;
        *) [[ "$name" =~ ^[a-z0-9_-]+$ ]] || die "invalid device name"
           DEVICE=$name; DEVICE_DIR=devices/$name ;;
    esac
    local conf=$DEVICE_DIR/device.conf
    [ -f "$conf" ] || die "no profile $conf (common/new-device.sh $name makes one)"
    # shellcheck disable=SC1090
    . "$conf"
    export DEVICE DEVICE_DIR KIND
    echo "${0##*/}: device $DEVICE -- $NAME ($KIND)"
}

have_userland() {
    [ -f userland/build.sh ] || die "incomplete monorepo: userland/build.sh is missing"
}
