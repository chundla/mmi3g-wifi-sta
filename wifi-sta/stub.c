/*
 * Phase-0 build check for the planned 88W8688 station driver.
 *
 * devnp-shim loads io-net drivers named devn-*.so through io_net_dll_entry.
 * This stub logs its options and refuses to attach. It never maps FPGA
 * registers, attaches interrupts or touches the radio, so loading it only
 * proves that the toolchain output is accepted by the shim.
 */
#include <errno.h>
#include <sys/io-net.h>
#include <sys/slog.h>
#include <sys/slogcodes.h>

static int stub_init(void *dll_hdl, dispatch_t *dpp, io_net_self_t *ion,
                     char *options)
{
    (void)dll_hdl;
    (void)dpp;
    (void)ion;
    slogf(_SLOGC_NETWORK, _SLOG_INFO,
          "devn-mv8688sta-stub: build check, options=\"%s\"; not attaching",
          options ? options : "");
    errno = ENODEV;
    return -1;
}

io_net_dll_entry_t io_net_dll_entry = { 2, stub_init, NULL };
