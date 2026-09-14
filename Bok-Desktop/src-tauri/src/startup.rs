//! Startup policy without Tauri, processes, clocks, or sleeping.
use std::time::Duration;

#[derive(Debug, PartialEq, Eq)]
pub(crate) enum Action {
    Wait,
    SlowNotice,
    RetryNotice,
    Navigate,
    BackendExited,
    Stop,
}

#[derive(Default)]
pub(crate) struct StartupWait {
    slow_notice: bool,
    retry_notice: bool,
    finished: bool,
}

impl StartupWait {
    pub(crate) fn next(
        &mut self,
        elapsed: Duration,
        stopped: bool,
        backend_exited: bool,
        ready: bool,
    ) -> Action {
        if self.finished {
            return Action::Wait;
        }
        if stopped {
            self.finished = true;
            return Action::Stop;
        }
        if backend_exited {
            self.finished = true;
            return Action::BackendExited;
        }
        if ready {
            // A failed navigation may be retried by the caller. A successful
            // navigation ends the worker, rather than starting a second one.
            return Action::Navigate;
        }
        if elapsed >= Duration::from_secs(120) && !self.retry_notice {
            self.retry_notice = true;
            self.slow_notice = true;
            return Action::RetryNotice;
        }
        if elapsed >= Duration::from_secs(15) && !self.slow_notice {
            self.slow_notice = true;
            return Action::SlowNotice;
        }
        Action::Wait
    }
}

pub(crate) fn loading_origin(scheme: &str, host: Option<&str>, path: &str) -> bool {
    path == "/loading.html"
        && matches!(
            (scheme, host),
            ("tauri", Some("localhost"))
                | ("http" | "https", Some("tauri.localhost"))
        )
}

#[cfg(test)]
mod tests {
    use super::{loading_origin, Action, StartupWait};
    use std::time::Duration;

    #[test]
    fn late_readiness_remains_actionable_after_both_notices() {
        let mut wait = StartupWait::default();
        assert_eq!(wait.next(Duration::from_secs(14), false, false, false), Action::Wait);
        assert_eq!(wait.next(Duration::from_secs(15), false, false, false), Action::SlowNotice);
        assert_eq!(wait.next(Duration::from_secs(40), false, false, true), Action::Navigate);
        // Simulate a failed navigation and continued startup.
        assert_eq!(wait.next(Duration::from_secs(120), false, false, false), Action::RetryNotice);
        assert_eq!(wait.next(Duration::from_secs(121), false, false, false), Action::Wait);
        assert_eq!(wait.next(Duration::from_secs(180), false, false, true), Action::Navigate);
    }

    #[test]
    fn retry_or_exit_wins_over_a_late_heartbeat() {
        let mut stopped = StartupWait::default();
        assert_eq!(stopped.next(Duration::ZERO, true, false, true), Action::Stop);
        assert_eq!(stopped.next(Duration::ZERO, false, false, true), Action::Wait);
        let mut exited = StartupWait::default();
        assert_eq!(exited.next(Duration::ZERO, false, true, true), Action::BackendExited);
        assert_eq!(exited.next(Duration::ZERO, false, false, true), Action::Wait);
    }

    #[test]
    fn a_new_attempt_has_no_stale_timeout_state() {
        let mut old_attempt = StartupWait::default();
        assert_eq!(old_attempt.next(Duration::from_secs(130), false, false, false), Action::RetryNotice);
        assert_eq!(old_attempt.next(Duration::from_secs(131), false, false, false), Action::Wait);
        let mut new_attempt = StartupWait::default();
        assert_eq!(new_attempt.next(Duration::ZERO, false, false, false), Action::Wait);
        assert_eq!(new_attempt.next(Duration::ZERO, false, false, true), Action::Navigate);
    }

    #[test]
    fn loading_page_origin_covers_both_desktop_platforms_only() {
        assert!(loading_origin("tauri", Some("localhost"), "/loading.html"));
        assert!(loading_origin("http", Some("tauri.localhost"), "/loading.html"));
        assert!(loading_origin("https", Some("tauri.localhost"), "/loading.html"));
        assert!(!loading_origin("http", Some("localhost"), "/loading.html"));
        assert!(!loading_origin("https", Some("example.com"), "/loading.html"));
        assert!(!loading_origin("tauri", Some("localhost"), "/quick-note.html"));
    }
}
