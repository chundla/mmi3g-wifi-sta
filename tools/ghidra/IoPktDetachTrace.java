// Offline trace for the exact saved HN+R io-pkt-v4-hc network stack.
// Output is derived analysis. Pseudocode signatures alone are not a callable ABI.
// Arguments: output-text-path
// Decompiles the interface clone/detach entry points and every function that
// loads the SIOCIFCREATE/SIOCIFDESTROY ioctl codes from a literal pool, to
// establish which mechanism removes a mounted io-pkt driver on this build.
// Never executes target instructions.
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileOptions;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.mem.Memory;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.Symbol;
import java.io.PrintWriter;
import java.util.LinkedHashSet;
import java.util.Set;
import java.util.TreeMap;

public class IoPktDetachTrace extends GhidraScript {
    private static final String SHA256 =
        "8c18545138ef90166559efcd6ef9d228dcf56b1ead07e7d05c1d43d30f6ca3b7";
    // Exported glue the stack offers its drivers, plus the NetBSD cloner layer.
    private static final String[] NAMES = {
        "if_clone_destroy", "if_clone_create", "if_clone_attach", "if_clone_detach",
        "dev_detach", "dev_detach_name", "dev_attach", "qnx_ifdetach",
        "ionet_umount", "nw_dlload_module", "if_detach", "log",
    };
    // SH loads 32-bit immediates from a literal pool, so the ioctl codes appear
    // as data words that instructions reference.
    private static final long[] IOCTLS = { 0x8090697aL, 0x80906979L };

    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length != 1) {
            throw new IllegalArgumentException("Expected output path");
        }
        if (!SHA256.equals(currentProgram.getExecutableSHA256())) {
            throw new IllegalArgumentException("Unexpected io-pkt SHA-256; refusing derived offsets");
        }
        if (!"SuperH4:LE:32:default".equals(currentProgram.getLanguageID().toString())) {
            throw new IllegalArgumentException("Expected SuperH4:LE:32:default import");
        }
        DecompInterface decompiler = new DecompInterface();
        DecompileOptions options = new DecompileOptions();
        options.setRespectReadOnly(true);
        decompiler.setOptions(options);
        try (PrintWriter out = new PrintWriter(args[0], "UTF-8")) {
            out.println("SHA256 " + SHA256);
            out.println("LANGUAGE " + currentProgram.getLanguageID());
            out.println("IMAGE_BASE " + currentProgram.getImageBase());
            out.println("Derived offline pseudocode; inferred types; no target execution.");
            if (!decompiler.openProgram(currentProgram)) {
                throw new IllegalStateException(decompiler.getLastMessage());
            }
            TreeMap<Address, Function> wanted = new TreeMap<>();
            for (String name : NAMES) {
                Function function = named(name);
                if (function == null) {
                    out.println("MISSING_SYMBOL " + name);
                    continue;
                }
                wanted.put(function.getEntryPoint(), function);
            }
            for (long code : IOCTLS) {
                for (Address pool : poolSites(code)) {
                    out.println(String.format("POOL %#x at %s", code, pool));
                    for (Reference reference : getReferencesTo(pool)) {
                        Function function = getFunctionContaining(reference.getFromAddress());
                        out.println("  LOADED_BY " + reference.getFromAddress()
                            + (function == null ? " (no function)" : " in " + function.getName()));
                        if (function != null) {
                            wanted.put(function.getEntryPoint(), function);
                        }
                    }
                }
            }
            for (Function function : wanted.values()) {
                monitor.checkCancelled();
                out.println("\nFUNCTION " + function.getEntryPoint() + " " + function.getName());
                out.println(decompile(decompiler, function));
                out.flush();
            }
            if (out.checkError()) throw new java.io.IOException("Writing trace failed");
        } finally {
            decompiler.dispose();
        }
    }

    private Function named(String name) {
        for (Symbol symbol : currentProgram.getSymbolTable().getGlobalSymbols(name)) {
            Function function = getFunctionAt(symbol.getAddress());
            if (function != null) return function;
        }
        return getGlobalFunctions(name).isEmpty() ? null : getGlobalFunctions(name).get(0);
    }

    private Set<Address> poolSites(long value) throws Exception {
        Set<Address> sites = new LinkedHashSet<>();
        Memory memory = currentProgram.getMemory();
        byte[] pattern = {
            (byte) value, (byte) (value >> 8), (byte) (value >> 16), (byte) (value >> 24),
        };
        Address cursor = currentProgram.getImageBase();
        while (cursor != null) {
            monitor.checkCancelled();
            Address hit = memory.findBytes(cursor, pattern, null, true, monitor);
            if (hit == null) break;
            sites.add(hit);
            cursor = hit.add(4);
        }
        return sites;
    }

    private String decompile(DecompInterface decompiler, Function function) {
        DecompileResults result = decompiler.decompileFunction(function, 120, monitor);
        return result.decompileCompleted()
            ? result.getDecompiledFunction().getC() : result.getErrorMessage();
    }
}
