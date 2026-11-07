// Validation de fichiers .sysml avec l'OMG SysML v2 Pilot Implementation.
//
// Usage : java -cp <jupyter-sysml-kernel-*-all.jar>:<dossier de cette classe> P9Validate <bibliotheque> <f1.sysml> [f2.sysml ...]
//
// Pourquoi ce lanceur plutot que SysMLInteractive.loadLibrary() :
//  - loadLibrary() ne trouve la bibliotheque que dans des dossiers dont le nom contient des espaces
//    ("Kernel Libraries"...), et sous Windows ces espaces sont mal decodes (%20) : rien n'est charge.
//    On charge donc nous-memes une copie de la bibliotheque aux noms sans espaces (Kernel_Libraries...).
//  - tout passe par reflexion : ce fichier compile avec n'importe quel javac >= 11, sans le jar.
//
// Les fichiers sont traites dans l'ordre donne, dans la meme session : un fichier peut importer
// les packages des fichiers precedents (ESpaceCommon d'abord, puis les use cases).
// Sortie : une ligne "ISSUE|<fichier>|<severite>|<ligne>|<colonne>|<message>" par probleme,
// puis "FILE|<fichier>|<nb erreurs>|<nb warnings>". Code retour 1 si au moins une erreur.

import java.io.File;
import java.lang.reflect.Method;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.List;

public class P9Validate {

    public static void main(String[] args) throws Exception {
        if (args.length < 2) {
            System.err.println("usage: P9Validate <library-dir> <file.sysml>...");
            System.exit(2);
        }
        String lib = new File(args[0]).getAbsolutePath().replace('\\', '/');
        if (!lib.endsWith("/")) lib += "/";

        Class<?> si = Class.forName("org.omg.sysml.interactive.SysMLInteractive");
        Object repl = si.getMethod("getInstance").invoke(null);
        Class.forName("org.omg.sysml.util.SysMLLibraryUtil")
            .getMethod("setModelLibraryDirectory", String.class).invoke(null, lib);
        si.getMethod("setVerbose", boolean.class).invoke(repl, false);
        Method readAll = si.getMethod("readAll", String.class, boolean.class, String.class);
        String[][] parts = {
            {"Kernel_Libraries", ".kerml"}, {"Systems_Library", ".sysml"}, {"Domain_Libraries", ".sysml"},
        };
        for (String[] p : parts) {
            stage("library", p[0]);
            File dir = new File(lib + p[0]);
            if (!dir.isDirectory()) {
                System.err.println("bibliotheque incomplete : " + dir);
                System.exit(2);
            }
            readAll.invoke(repl, dir.getPath(), false, p[1]);
        }

        Method process = si.getMethod("process", String.class);
        int totalErrors = 0;
        for (int i = 1; i < args.length; i++) {
            File f = new File(args[i]);
            stage("file", f.getName());
            String text = new String(Files.readAllBytes(f.toPath()), StandardCharsets.UTF_8);
            Object result = process.invoke(repl, text);
            Class<?> rc = result.getClass();
            Exception ex = (Exception) rc.getMethod("getException").invoke(result);
            int errors = 0, warnings = 0;
            if (ex != null) {
                errors++;
                emit(f.getName(), "ERROR", 0, 0, "exception: " + ex);
            }
            for (Object issue : (List<?>) rc.getMethod("getIssues").invoke(result)) {
                Class<?> ic = Class.forName("org.eclipse.xtext.validation.Issue");
                String sev = String.valueOf(ic.getMethod("getSeverity").invoke(issue));
                Integer line = (Integer) ic.getMethod("getLineNumber").invoke(issue);
                Integer col = (Integer) ic.getMethod("getColumn").invoke(issue);
                String msg = String.valueOf(ic.getMethod("getMessage").invoke(issue));
                if (sev.equals("ERROR")) errors++;
                else if (sev.equals("WARNING")) warnings++;
                emit(f.getName(), sev, line == null ? 0 : line, col == null ? 0 : col, msg);
            }
            System.out.println("FILE|" + f.getName() + "|" + errors + "|" + warnings);
            totalErrors += errors;
        }
        System.out.flush();
        System.exit(totalErrors > 0 ? 1 : 0);
    }

    /** Avancement (lu en direct par sysml_parser.py) : STAGE|library|nom ou STAGE|file|nom. */
    private static void stage(String kind, String name) {
        System.out.println("STAGE|" + kind + "|" + name);
        System.out.flush();
    }

    private static void emit(String file, String sev, int line, int col, String msg) {
        System.out.println("ISSUE|" + file + "|" + sev + "|" + line + "|" + col + "|" + msg.replace('\n', ' '));
    }
}
