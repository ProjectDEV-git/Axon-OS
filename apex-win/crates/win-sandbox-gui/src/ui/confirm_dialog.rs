use gtk::prelude::*;
use gtk::{self, glib};
use win_sandbox_common::tier::Tier;

/// Result of the user's confirmation dialog choice.
#[derive(Debug, Clone)]
pub struct ConfirmResult {
    pub tier: Tier,
    pub remember: bool,
}

/// Show a confirmation dialog for an unmapped binary.
///
/// Presents the user with:
/// - Binary name and hash
/// - "Run Sandboxed (Tier 2)" button
/// - "Run Direct (Tier 0)" button
/// - "Deny" button
/// - "Remember this choice" checkbox
///
/// Returns the user's choice via a glib channel receiver.
pub fn show(binary_name: &str, hash: &str, path: &str) -> ConfirmResult {
    let result = std::sync::Mutex::new(Some(ConfirmResult {
        tier: Tier::Tier2,
        remember: false,
    }));
    let result = std::sync::Arc::new(result);

    let dialog = gtk::Window::builder()
        .title("win-sandbox-runner — Confirm Execution")
        .modal(true)
        .default_width(480)
        .default_height(320)
        .resizable(false)
        .build();

    let vbox = gtk::Box::builder()
        .orientation(gtk::Orientation::Vertical)
        .spacing(12)
        .margin_top(24)
        .margin_bottom(24)
        .margin_start(24)
        .margin_end(24)
        .build();

    // Header
    let header = gtk::Label::new(Some("Unmapped Binary Detected"));
    header.add_css_class("title-2");
    vbox.append(&header);

    // Binary info
    let info = format!(
        "<b>Name:</b> {}\n<b>Hash:</b> {}…{}\n<b>Path:</b> {}",
        glib::markup_escape_text(binary_name),
        &hash[..8.min(hash.len())],
        if hash.len() > 8 {
            &hash[hash.len() - 8..]
        } else {
            ""
        },
        glib::markup_escape_text(path),
    );
    let info_label = gtk::Label::new(None);
    info_label.set_markup(&info);
    info_label.set_selectable(true);
    info_label.set_xalign(0.0);
    vbox.append(&info_label);

    // Description
    let desc = gtk::Label::new(Some(
        "This binary is not in your rules.json. Choose how to run it:",
    ));
    desc.set_wrap(true);
    desc.set_xalign(0.0);
    vbox.append(&desc);

    // Remember checkbox
    let remember_check = gtk::CheckButton::builder()
        .label("Remember this choice (add to rules.json)")
        .active(false)
        .build();
    vbox.append(&remember_check);

    // Button box
    let btn_box = gtk::Box::builder()
        .orientation(gtk::Orientation::Horizontal)
        .spacing(8)
        .halign(gtk::Align::End)
        .build();

    let result_ref = result.clone();
    let dialog_ref = dialog.clone();
    let remember_ref = remember_check.clone();

    // Deny button
    let deny_btn = gtk::Button::with_label("Deny");
    deny_btn.add_css_class("destructive-action");
    {
        let result_ref = result_ref.clone();
        let dialog_ref = dialog_ref.clone();
        let remember_ref = remember_ref.clone();
        deny_btn.connect_clicked(move |_| {
            if let Ok(mut r) = result_ref.lock() {
                *r = Some(ConfirmResult {
                    tier: Tier::Tier0, // Won't be used, but set a default
                    remember: remember_ref.is_active(),
                });
            }
            dialog_ref.close();
        });
    }
    btn_box.append(&deny_btn);

    // Run Direct (Tier 0) button
    let direct_btn = gtk::Button::with_label("Run Direct (Tier 0)");
    {
        let result_ref = result_ref.clone();
        let dialog_ref = dialog_ref.clone();
        let remember_ref = remember_ref.clone();
        direct_btn.connect_clicked(move |_| {
            if let Ok(mut r) = result_ref.lock() {
                *r = Some(ConfirmResult {
                    tier: Tier::Tier0,
                    remember: remember_ref.is_active(),
                });
            }
            dialog_ref.close();
        });
    }
    btn_box.append(&direct_btn);

    // Run Sandboxed (Tier 2) button — suggested default
    let sandbox_btn = gtk::Button::with_label("Run Sandboxed (Tier 2)");
    sandbox_btn.add_css_class("suggested-action");
    {
        let result_ref = result_ref.clone();
        let dialog_ref = dialog_ref.clone();
        let remember_ref = remember_ref.clone();
        sandbox_btn.connect_clicked(move |_| {
            if let Ok(mut r) = result_ref.lock() {
                *r = Some(ConfirmResult {
                    tier: Tier::Tier2,
                    remember: remember_ref.is_active(),
                });
            }
            dialog_ref.close();
        });
    }
    btn_box.append(&sandbox_btn);

    vbox.append(&btn_box);
    dialog.set_child(Some(&vbox));

    // Present and run (blocking — the CLI runner waits for the user)
    dialog.present();

    // We need to run a nested main loop to block until the dialog is closed.
    // This is the standard pattern for modal GTK dialogs in Rust.
    let main_context = glib::MainContext::default();
    while dialog.is_visible() {
        main_context.iteration(true);
    }

    // Extract result
    let locked = result.lock();
    match locked {
        Ok(guard) => guard.clone().unwrap_or(ConfirmResult {
            tier: Tier::Tier2,
            remember: false,
        }),
        Err(_) => ConfirmResult {
            tier: Tier::Tier2,
            remember: false,
        },
    }
}
