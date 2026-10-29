/*
 * fvl_init.vpi - FPGA Virtual Lab helper for Icarus Verilog.
 *
 * $fvl_power_on_init(scope) walks every module below `scope` and deposits 0
 * into each register / integer / register-array word whose value contains
 * x or z bits. This models FPGA configuration, where every flip-flop and
 * block RAM starts at 0 unless an explicit initial value is given.
 * Registers that already hold a known value (from `reg r = 1;` or an
 * initial block that ran first) are left untouched.
 */
#include <string.h>
#include <stdlib.h>
#include "vpi_user.h"

static long fvl_count;

static int has_xz(vpiHandle h)
{
    s_vpi_value v;
    v.format = vpiBinStrVal;
    vpi_get_value(h, &v);
    if (!v.value.str) return 0;
    for (const char *p = v.value.str; *p; p++)
        if (*p == 'x' || *p == 'X' || *p == 'z' || *p == 'Z') return 1;
    return 0;
}

static void zero_fill(vpiHandle h)
{
    int size = vpi_get(vpiSize, h);
    char *bits;
    s_vpi_value v;
    if (size <= 0 || !has_xz(h)) return;
    bits = (char *)malloc((size_t)size + 1);
    if (!bits) return;
    s_vpi_value cur;
    cur.format = vpiBinStrVal;
    vpi_get_value(h, &cur);
    /* Keep known bits, replace only x/z bits by 0. */
    for (int i = 0; i < size; i++) {
        char c = (cur.value.str && (int)strlen(cur.value.str) == size) ? cur.value.str[i] : 'x';
        bits[i] = (c == '1') ? '1' : '0';
    }
    bits[size] = 0;
    v.format = vpiBinStrVal;
    v.value.str = bits;
    vpi_put_value(h, &v, NULL, vpiNoDelay);
    free(bits);
    fvl_count++;
}

static void walk_items(vpiHandle scope, int type)
{
    vpiHandle it = vpi_iterate(type, scope), h;
    if (!it) return;
    while ((h = vpi_scan(it)) != NULL) {
        if (type == vpiMemory || type == vpiRegArray) {
            vpiHandle wi = vpi_iterate(vpiMemoryWord, h), w;
            if (!wi) wi = vpi_iterate(vpiReg, h);
            if (wi) while ((w = vpi_scan(wi)) != NULL) zero_fill(w);
        } else {
            zero_fill(h);
        }
    }
}

static void walk(vpiHandle scope)
{
    vpiHandle it, h;
    walk_items(scope, vpiReg);
    walk_items(scope, vpiIntegerVar);
    walk_items(scope, vpiMemory);
    walk_items(scope, vpiRegArray);
    it = vpi_iterate(vpiInternalScope, scope);
    if (it) while ((h = vpi_scan(it)) != NULL) walk(h);
}

static PLI_INT32 calltf(PLI_BYTE8 *user)
{
    vpiHandle call = vpi_handle(vpiSysTfCall, NULL);
    vpiHandle args = vpi_iterate(vpiArgument, call);
    vpiHandle scope = args ? vpi_scan(args) : NULL;
    (void)user;
    fvl_count = 0;
    if (scope) {
        walk(scope);
        vpi_free_object(args);
    }
    vpi_printf("FVL_INIT %ld\n", fvl_count);
    return 0;
}

static void fvl_register(void)
{
    s_vpi_systf_data tf;
    memset(&tf, 0, sizeof tf);
    tf.type = vpiSysTask;
    tf.tfname = "$fvl_power_on_init";
    tf.calltf = calltf;
    vpi_register_systf(&tf);
}

void (*vlog_startup_routines[])(void) = { fvl_register, 0 };
