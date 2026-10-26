use gtk::prelude::*;
use gtk::{self, glib};

/// Result from the setup progress dialog.
pub struct SetupProgressHandle {
    pub window: gtk::Window,
    pub label: gtk::Label,
    pub progress_bar: gtk::ProgressBar,
}

/// Create and show a setup progress dialog.
/// Returns a handle so the caller can update the progress.
pub fn show(app_name: &str) -> SetupProgressHandle {
    let window = gtk::Window::builder()
        .title(format!("Setting up {app_name}"))
        .default_width(450)
        .default_height(180)
        .resizable(false)
        .modal(true)
        .build();

    let vbox = gtk::Box::builder()
        .orientation(gtk::Orientation::Vertical)
        .spacing(12)
        .margin_top(24)
        .margin_bottom(24)
        .margin_start(24)
        .margin_end(24)
        .build();

    // App name header
    let header = gtk::Label::builder()
        .label(format!("<b>Setting up {app_name}</b>"))
        .use_markup(true)
        .build();
    vbox.append(&header);

    // Status label
    let label = gtk::Label::builder().label("Preparing...").build();
    vbox.append(&label);

    // Progress bar
    let progress_bar = gtk::ProgressBar::builder().fraction(0.0).build();
    vbox.append(&progress_bar);

    // Spinner for indeterminate state
    let spinner = gtk::Spinner::builder().spinning(true).build();
    vbox.append(&spinner);

    window.set_child(Some(&vbox));
    window.present();

    SetupProgressHandle {
        window,
        label,
        progress_bar,
    }
}

impl SetupProgressHandle {
    /// Update the status text and progress fraction.
    pub fn update(&self, step: &str, progress: f64) {
        self.label.set_text(step);
        if progress < 0.0 {
            self.progress_bar.pulse();
        } else {
            self.progress_bar.set_fraction(progress.clamp(0.0, 1.0));
        }
    }

    /// Mark setup as complete and close after a delay.
    pub fn complete(&self, success: bool, summary: &str) {
        if success {
            self.label.set_text(&format!("✓ {summary}"));
            self.progress_bar.set_fraction(1.0);
        } else {
            self.label.set_text(&format!("⚠ {summary}"));
        }

        // Auto-close after 2 seconds
        let window = self.window.clone();
        glib::timeout_add_local_once(std::time::Duration::from_millis(2000), move || {
            window.close();
        });
    }
}
