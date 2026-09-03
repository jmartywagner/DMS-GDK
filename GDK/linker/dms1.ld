OUTPUT_ARCH(m68k)
ENTRY(_start)
MEMORY
{
    ROM (rx)  : ORIGIN = 0x000000, LENGTH = 1024K
    RAM (rwx) : ORIGIN = 0x100000, LENGTH = 64K
}
SECTIONS
{
    .vectors ORIGIN(ROM) : ALIGN(4) { KEEP(*(.vectors)) } > ROM
    .text : ALIGN(2) {
        KEEP(*(.text.startup))
        *(.text*)
        *(.rodata*)
        . = ALIGN(2);
    } > ROM
    .data : ALIGN(2) {
        _sdata = .;
        *(.data*)
        . = ALIGN(2);
        _edata = .;
    } > RAM AT> ROM
    _sidata = LOADADDR(.data);
    .bss (NOLOAD) : ALIGN(2) {
        _sbss = .;
        *(.bss*)
        *(COMMON)
        . = ALIGN(2);
        _ebss = .;
    } > RAM
    __stack_top = ORIGIN(RAM) + LENGTH(RAM) - 4;
    /DISCARD/ : { *(.comment*) *(.note*) *(.eh_frame*) }
}
/* Garde-fou matériel : données + BSS doivent laisser 8 Kio à la pile et aux
   temporaires runtime. Le linker refuse la cartouche au lieu de masquer un
   dépassement de la WRAM DMS-1 de 64 Kio. */
ASSERT(_ebss <= __stack_top - 0x2000, "DMS-1 WRAM overflow: leave 8 KiB for stack/runtime")
