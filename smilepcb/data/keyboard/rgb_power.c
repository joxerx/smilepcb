#include QMK_KEYBOARD_H
#include "usb_device_state.h"

static uint8_t power_state;
static uint32_t power_started;

static void rgb_power_off(void) {
    /* QMK may flush black frames while disabled. Isolate DIN electrically. */
    writePinLow(F1);
    rgb_matrix_disable_noeeprom();
    writePinLow(B9);
    writePinLow(B8);
    power_state = 0;
}

void keyboard_pre_init_user(void) {
    setPinOutput(F1);
    writePinLow(F1);
    setPinOutput(B9);
    writePinLow(B9);
    setPinOutput(B8);
    writePinLow(B8);
#if defined(STM32F072xB)
    /* HSI48 is enabled by mcuconf.h. CRS defaults select the USB SOF
       reference with reload 47999; enable automatic oscillator trimming. */
    RCC->APB1ENR |= RCC_APB1ENR_CRSEN;
    CRS->CR |= CRS_CR_AUTOTRIMEN | CRS_CR_CEN;
#endif
}

void keyboard_post_init_user(void) {
    rgb_power_off();
}

void suspend_power_down_user(void) {
    rgb_power_off();
}

void housekeeping_task_user(void) {
    if (usb_device_state_get_configure_state() != USB_DEVICE_STATE_CONFIGURED) {
        if (power_state) rgb_power_off();
        return;
    }
    if (power_state == 0) {
        writePinHigh(B9);
        power_started = timer_read32();
        power_state = 1;
    } else if (power_state == 1 && timer_elapsed32(power_started) >= 100) {
        /* Keep OE low through the CT-controlled supply ramp. QMK's disabled
           renderer can transmit black frames during this interval. */
        writePinLow(B8);
        rgb_matrix_enable_noeeprom();
        /* Persisted EEPROM HSV can predate this board's current limit. The
           QMK setter operates only after enabling; OE remains low here. */
        uint8_t value = rgb_matrix_get_val();
        if (value > RGB_MATRIX_MAXIMUM_BRIGHTNESS) value = RGB_MATRIX_MAXIMUM_BRIGHTNESS;
        rgb_matrix_sethsv_noeeprom(rgb_matrix_get_hue(), rgb_matrix_get_sat(), value);
        writePinHigh(F1);
        power_state = 2;
    }
}
