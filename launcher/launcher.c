/*
 * Scribe Dictate.app launcher.
 *
 * A tiny native executable that is the app bundle's main binary. It starts the
 * Python menu-bar app (menubar.py) from the project folder as a child process.
 * Because the child inherits this app as its "responsible process", macOS asks
 * for Microphone / Input Monitoring / Accessibility on behalf of
 * "Scribe Dictate" — not Python or Terminal.
 *
 * - stdout/stderr go to ~/Library/Logs/ScribeDictate.log
 * - crashes are restarted (max 5 within 60s); a clean quit (exit 0) or a
 *   config error (exit 78) is not.
 *
 * PROJECT_DIR and PYTHON_BIN are injected at compile time by build_app.sh.
 */
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#ifndef PROJECT_DIR
#error "PROJECT_DIR must be defined"
#endif
#ifndef PYTHON_BIN
#error "PYTHON_BIN must be defined"
#endif

extern char **environ;
static volatile pid_t child = 0;
static volatile sig_atomic_t stopping = 0;

static void forward_signal(int sig) {
    stopping = 1;
    if (child > 0) kill(child, sig);
}

int main(void) {
    const char *home = getenv("HOME");
    char log_path[2048];
    snprintf(log_path, sizeof log_path, "%s/Library/Logs/ScribeDictate.log", home ? home : "/tmp");
    int fd = open(log_path, O_WRONLY | O_CREAT | O_APPEND, 0644);
    if (fd >= 0) {
        dup2(fd, STDOUT_FILENO);
        dup2(fd, STDERR_FILENO);
        close(fd);
    }

    if (chdir(PROJECT_DIR) != 0) {
        fprintf(stderr, "[launcher] cannot cd to %s: %s\n", PROJECT_DIR, strerror(errno));
        return 1;
    }

    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_handler = forward_signal;
    sigaction(SIGTERM, &sa, NULL);
    sigaction(SIGINT, &sa, NULL);
    sigaction(SIGHUP, &sa, NULL);

    char *argv[] = {(char *)PYTHON_BIN, (char *)"-u", (char *)PROJECT_DIR "/menubar.py", NULL};

    time_t window_start = time(NULL);
    int crashes = 0;

    for (;;) {
        pid_t pid;
        int rc = posix_spawn(&pid, PYTHON_BIN, NULL, NULL, argv, environ);
        if (rc != 0) {
            fprintf(stderr, "[launcher] failed to start %s: %s\n", PYTHON_BIN, strerror(rc));
            return 1;
        }
        child = pid;

        int status = 0;
        while (waitpid(pid, &status, 0) < 0) {
            if (errno != EINTR) break;
        }
        child = 0;

        if (stopping) return 0;
        if (WIFEXITED(status)) {
            int code = WEXITSTATUS(status);
            if (code == 0 || code == 78) return code;
            fprintf(stderr, "[launcher] menubar.py exited with code %d\n", code);
        } else if (WIFSIGNALED(status)) {
            fprintf(stderr, "[launcher] menubar.py killed by signal %d\n", WTERMSIG(status));
        }

        time_t now = time(NULL);
        if (now - window_start > 60) {
            window_start = now;
            crashes = 0;
        }
        if (++crashes > 5) {
            fprintf(stderr, "[launcher] crashed %d times within 60s, giving up\n", crashes);
            return 1;
        }
        fprintf(stderr, "[launcher] restarting in 2s…\n");
        sleep(2);
    }
}
