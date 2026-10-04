OUTPUT_FORMAT("elf32-shbig-linux")
ENTRY(_start)
SECTIONS
{
    . = 0x060F6800;
    .text : { *(.text.entry) *(.text .text.*) }
    .rodata : { *(.rodata .rodata.*) }
    .data : { *(.data .data.*) }
    .bss : { *(.bss .bss.*) *(COMMON) }
    _end = .;
    /DISCARD/ : { *(.comment) *(.note*) *(.eh_frame*) }
}
ASSERT(_end <= 0x060F7C00, "subtitle code overruns its 5 KB (the index starts at BASE+0x1400)")
