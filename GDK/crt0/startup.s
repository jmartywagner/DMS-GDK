/* GNU m68k startup for DMS-1. 68000 reset reads SSP at 0 and PC at 4. */
.section .vectors,"a"
.global __vectors
__vectors:
    .long __stack_top
    .long _start
    .rept 62
    .long _default_handler
    .endr

.section .text.startup,"ax"
.global _start
.type _start,@function
_start:
    lea _sidata,%a0
    lea _sdata,%a1
    lea _edata,%a2
1:
    cmpa.l %a2,%a1
    bcc 2f
    move.b (%a0)+,(%a1)+
    bra 1b
2:
    lea _sbss,%a1
    lea _ebss,%a2
    moveq #0,%d0
3:
    cmpa.l %a2,%a1
    bcc 4f
    move.b %d0,(%a1)+
    bra 3b
4:
    jsr main
5:
    bra 5b

_default_handler:
    bra _default_handler
