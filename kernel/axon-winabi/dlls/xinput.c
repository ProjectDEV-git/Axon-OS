#include <stdint.h>
#include <string.h>

#define XINPUT_GAMEPAD_DPAD_UP    0x0001
#define XINPUT_GAMEPAD_DPAD_DOWN  0x0002
#define XINPUT_GAMEPAD_DPAD_LEFT  0x0004
#define XINPUT_GAMEPAD_DPAD_RIGHT 0x0008
#define XINPUT_GAMEPAD_START      0x0010
#define XINPUT_GAMEPAD_BACK       0x0020
#define XINPUT_GAMEPAD_LEFT_THUMB 0x0040
#define XINPUT_GAMEPAD_RIGHT_THUMB 0x0080
#define XINPUT_GAMEPAD_LEFT_SHOULDER  0x0100
#define XINPUT_GAMEPAD_RIGHT_SHOULDER 0x0200
#define XINPUT_GAMEPAD_A           0x1000
#define XINPUT_GAMEPAD_B           0x2000
#define XINPUT_GAMEPAD_X           0x4000
#define XINPUT_GAMEPAD_Y           0x8000

#define ERROR_SUCCESS 0
#define ERROR_DEVICE_NOT_CONNECTED 1167

typedef struct {
    uint16_t wButtons;
    uint8_t  bLeftTrigger;
    uint8_t  bRightTrigger;
    int16_t  sThumbLX;
    int16_t  sThumbLY;
    int16_t  sThumbRX;
    int16_t  sThumbRY;
} XINPUT_GAMEPAD;

typedef struct {
    uint32_t dwPacketNumber;
    XINPUT_GAMEPAD Gamepad;
} XINPUT_STATE;

typedef struct {
    uint16_t wButtons;
    uint8_t  bLeftTrigger;
    uint8_t  bRightTrigger;
} XINPUT_VIBRATION;

typedef struct {
    uint32_t dwType;
    uint32_t dwSubType;
    uint32_t dwFlags;
    XINPUT_GAMEPAD Gamepad;
    uint32_t dwVibrationMotorSpeed;
} XINPUT_CAPABILITIES;

__attribute__((visibility("default")))
uint32_t XInputGetState(uint32_t dwUserIndex, XINPUT_STATE *pState) {
    if (!pState) return ERROR_DEVICE_NOT_CONNECTED;
    memset(pState, 0, sizeof(*pState));
    return ERROR_SUCCESS;
}

__attribute__((visibility("default")))
uint32_t XInputSetState(uint32_t dwUserIndex, XINPUT_VIBRATION *pVibration) {
    return ERROR_SUCCESS;
}

__attribute__((visibility("default")))
uint32_t XInputGetCapabilities(uint32_t dwUserIndex, uint32_t dwFlags, XINPUT_CAPABILITIES *pCapabilities) {
    if (!pCapabilities) return ERROR_DEVICE_NOT_CONNECTED;
    memset(pCapabilities, 0, sizeof(*pCapabilities));
    return ERROR_SUCCESS;
}

__attribute__((visibility("default")))
void XInputEnable(uint32_t enable) {
}

__attribute__((visibility("default")))
uint32_t XInputGetDSoundAudioDeviceGuids(uint32_t dwUserIndex, void *pDSoundRenderGuid, void *pDSoundCaptureGuid) {
    return ERROR_DEVICE_NOT_CONNECTED;
}

__attribute__((visibility("default")))
uint32_t XInputGetBatteryInformation(uint32_t dwUserIndex, uint8_t devType, void *pBatteryInformation) {
    return ERROR_DEVICE_NOT_CONNECTED;
}

__attribute__((visibility("default")))
uint32_t XInputGetKeystroke(uint32_t dwUserIndex, uint32_t dwReserved, void *pKeystroke) {
    return ERROR_DEVICE_NOT_CONNECTED;
}
