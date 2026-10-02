/* Do-nothing controller so the Red Pitaya menu lists the app.
 *
 * The menu only shows apps whose controllerhf.so loads and exports these
 * three functions. The real app is the Python server on port 8000; the tile's
 * index.html just redirects there and never starts this controller.
 *
 * Built on the Red Pitaya by deploy/install.sh:
 *   gcc -shared -fPIC -o controllerhf.so controller_stub.c
 */

int rp_app_init(void) { return 0; }

int rp_app_exit(void) { return 0; }

const char *rp_app_desc(void) { return "Spectrofluorometer (link to port 8000)"; }
