OUTPUT_FORMAT("elf32-shbig-linux")
ENTRY(_start)
SECTIONS
{
    . = 0x060FC800;
    .text : { *(.text.entry) *(.text .text.*) }
    .rodata : { *(.rodata .rodata.*) }
    .data : { *(.data .data.*) }
    .bss : { *(.bss .bss.*) *(COMMON) }
    _end = .;
    /DISCARD/ : { *(.comment) *(.note*) *(.eh_frame*) }
}
ASSERT(_end <= 0x060FE800, "subtitle code overruns its 8 KB (the cell use table starts at BASE+0x2000)")
