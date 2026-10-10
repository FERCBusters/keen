"""Run the PDF parser in a resource-limited child, outside API worker threads."""
import os
import resource
import sys


def main():
    source, output = sys.argv[1:]
    for kind, limit in ((resource.RLIMIT_AS, 512 * 1024 * 1024),
                        (resource.RLIMIT_CPU, 10),
                        (resource.RLIMIT_FSIZE, 16 * 1024 * 1024),
                        (resource.RLIMIT_CORE, 0)):
        resource.setrlimit(kind, (limit, limit))
    os.execvp('pdftoppm', ['pdftoppm', '-f', '1', '-l', '1', '-singlefile',
                         '-scale-to', '1600', '-png', source, output])


if __name__ == '__main__':
    main()
