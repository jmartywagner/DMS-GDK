#ifndef DMS_PAD_H
#define DMS_PAD_H
#include <stdint.h>
#define DMS_BUTTON_UP      0x01
#define DMS_BUTTON_DOWN    0x02
#define DMS_BUTTON_LEFT    0x04
#define DMS_BUTTON_RIGHT   0x08
#define DMS_BUTTON_A       0x10
#define DMS_BUTTON_B       0x20
#define DMS_BUTTON_C       0x40
#define DMS_BUTTON_START   0x80
/* Sérigraphie officielle DMS-1. Les noms C/START restent des alias source
   pour les anciens projets et les dispositions clavier historiques. */
#define DMS_BUTTON_PLUS     DMS_BUTTON_C
#define DMS_BUTTON_MULTIPLY DMS_BUTTON_START
#define DMS_BUTTON_X        DMS_BUTTON_MULTIPLY
uint8_t PAD_read(void);
uint8_t PAD_pressed(uint8_t mask);
uint8_t PAD_released(uint8_t mask);
#endif
