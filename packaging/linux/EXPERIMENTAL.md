# Experimental Linux build

The managed Linux graph has been built and packaged in an Ubuntu 22.04 x64
container. Its 14 CTest checks and dependency audit pass; extracted executables
discover their own runtime data from an unrelated directory. Large BSP kernels
remain external and their absence is reported by `--doctor` with exit code 3.

Fresh-machine qualification of the public community candidate remains pending.
The managed graph uses IPOPT 3.14.11; results from the separate IPOPT 3.14.19 CI
graph do not qualify this package, or vice versa.

Other distributions, architectures, desktop environments and optional Python
extensions remain unqualified. Report your distribution, architecture, compiler,
build command and complete error output with Linux-specific issues.
