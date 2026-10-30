// Offline trace of the io-net hosting path on the 9411 unit: the exact saved
// HN+R devnp-shim.so (the io-pkt shim that hosts io-net drivers) and
// devn-asix.so (a working io-net driver behind it), for the station driver.
// Output is derived analysis. Pseudocode signatures alone are not a callable ABI.
// Arguments: output-text-path
// Decompiles every analyzed function, after promoting code addresses that are
// stored as pointers (decompiler-visible or relocated data); never executes
// target instructions.
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileOptions;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.mem.MemoryBlock;
import ghidra.program.model.reloc.Relocation;
import java.io.PrintWriter;
import java.util.ArrayList;
import java.util.Iterator;
import java.util.List;
import java.util.TreeSet;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public class IoNetTrace extends GhidraScript {
    private static final java.util.Map<String, String> PINNED = java.util.Map.of(
        "00af614e34a3a0de15337135debce697e7cedb4244346b6f218d4de1cfbb5d8b", "devnp-shim.so",
        "085f2658a7649fcbd1ca0e08267a83d86534855d3c86f78f3a1826e8a3df241a", "devn-asix.so");
    // PIC code builds callback addresses from GOT offsets, so Ghidra records no
    // reference to them; the decompiler still prints the taken address.
    // Calls into absorbed code or unnamed PLT stubs print as &UNK_ or a cast
    // constant instead.
    private static final Pattern TAKEN_LABEL = Pattern.compile(
        "&(?:LAB|UNK)_([0-9a-f]{8})|\\(code \\*\\)0x([0-9a-f]+)\\)");

    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length != 1) {
            throw new IllegalArgumentException("Expected output path");
        }
        String sha256 = currentProgram.getExecutableSHA256();
        if (!PINNED.containsKey(sha256)) {
            throw new IllegalArgumentException("Unexpected object SHA-256; refusing derived offsets");
        }
        if (!"SuperH4:LE:32:default".equals(currentProgram.getLanguageID().toString())) {
            throw new IllegalArgumentException("Expected SuperH4:LE:32:default import");
        }
        DecompInterface decompiler = new DecompInterface();
        DecompileOptions options = new DecompileOptions();
        options.setRespectReadOnly(true);
        decompiler.setOptions(options);
        try (PrintWriter out = new PrintWriter(args[0], "UTF-8")) {
            out.println("OBJECT " + PINNED.get(sha256));
            out.println("SHA256 " + sha256);
            out.println("LANGUAGE " + currentProgram.getLanguageID());
            out.println("IMAGE_BASE " + currentProgram.getImageBase()
                + " (file offset = address - image base for the text segment)");
            out.println("PIC calls appear as computed addresses; callee identities need instruction-level checks.");
            out.println("Derived offline pseudocode; inferred types; no target execution.");
            if (!decompiler.openProgram(currentProgram)) {
                throw new IllegalStateException(decompiler.getLastMessage());
            }
            // Promoting a label splits the function that absorbed it, so repeat
            // until no new callback appears, then write one consistent pass.
            while (promoteCallbacks(decompiler, out)) {
                decompiler.flushCache();
            }
            for (Function function : currentProgram.getFunctionManager().getFunctions(true)) {
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

    private String decompile(DecompInterface decompiler, Function function) {
        DecompileResults result = decompiler.decompileFunction(function, 60, monitor);
        return result.decompileCompleted()
            ? result.getDecompiledFunction().getC() : result.getErrorMessage();
    }

    // The section-stripped import leaves no block marked executable; the R-E
    // load segment is the block at the image base.
    private boolean isTextBlock(MemoryBlock block) {
        return block.getStart().equals(currentProgram.getImageBase());
    }

    private boolean isTextAddress(Address address) {
        MemoryBlock block = getMemoryBlock(address);
        return block != null && isTextBlock(block);
    }

    private Address codeEnd;

    private boolean promoteCallbacks(DecompInterface decompiler, PrintWriter out)
            throws Exception {
        TreeSet<Address> targets = new TreeSet<>();
        if (codeEnd == null) {
            for (Function function : currentProgram.getFunctionManager().getFunctions(true)) {
                Address end = function.getBody().getMaxAddress();
                if (!function.isExternal() && isTextAddress(end)
                        && (codeEnd == null || end.compareTo(codeEnd) > 0)) {
                    codeEnd = end;
                }
            }
            out.println("CODE_END " + codeEnd);
        }
        List<Function> functions = new ArrayList<>();
        currentProgram.getFunctionManager().getFunctions(true).forEach(functions::add);
        for (Function function : functions) {
            monitor.checkCancelled();
            if (function.isExternal() || function.isThunk()) continue;
            Matcher m = TAKEN_LABEL.matcher(decompile(decompiler, function));
            while (m.find()) {
                String hex = m.group(1) != null ? m.group(1) : m.group(2);
                Address dest = toAddr(Long.parseLong(hex, 16));
                if (isTextAddress(dest) && getFunctionAt(dest) == null) {
                    targets.add(dest);
                }
            }
        }
        // Relocated data words hold the remaining callbacks, such as the
        // autoconf attach/detach entries.
        for (Iterator<Relocation> it = currentProgram.getRelocationTable().getRelocations();
                it.hasNext();) {
            Address site = it.next().getAddress();
            MemoryBlock siteBlock = getMemoryBlock(site);
            if (siteBlock == null || isTextBlock(siteBlock)) continue;
            Address dest = toAddr(getInt(site) & 0xffffffffL);
            // Read-only strings follow the code in the same segment.
            if (isTextAddress(dest) && dest.compareTo(codeEnd) <= 0
                    && (dest.getOffset() & 1) == 0 && getFunctionAt(dest) == null) {
                targets.add(dest);
            }
        }
        boolean created = false;
        for (Address dest : targets) {
            if (getInstructionAt(dest) == null && !disassemble(dest)) continue;
            if (createFunction(dest, null) != null) {
                out.println("CREATED_FUNCTION " + dest);
                created = true;
            }
        }
        return created;
    }
}
