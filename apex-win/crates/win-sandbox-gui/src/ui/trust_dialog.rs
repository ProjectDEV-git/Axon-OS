use gtk::prelude::*;
use gtk::{self, glib};

/// Result of the trust-level dialog choice.
#[derive(Debug, Clone)]
pub struct TrustResult {
    pub tier: u8,
    pub network: bool,
    pub gpu: bool,
    pub remember: bool,
}

/// Show a trust-level selector dialog for an untrusted path.
///
/// Presents the user with:
/// - Binary info (name, hash, path)
/// - Tier radio buttons (0–3)
/// - Network toggle
/// - GPU toggle
/// - "Remember" checkbox
pub fn show(binary_name: &str, hash: &str, path: &str, suggested_tier: u8) -> TrustResult {
    let result = std::sync::Mutex::new(Some(TrustResult {
        tier: suggested_tier,
        network: false,
        gpu: false,
        remember: false,
    }));
    let result = std::sync::Arc::new(result);

    let dialog = gtk::Window::builder()
        .title("win-sandbox-runner — Trust Settings")
        .modal(true)
        .default_width(520)
        .default_height(420)
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
    let header = gtk::Label::new(Some("Set Trust Level"));
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

    // Tier description labels
    let tier_descriptions = [
        ("Tier 0 — None", "Direct wine exec, no isolation"),
        ("Tier 1 — Landlock", "Filesystem restrictions via LSM"),
        (
            "Tier 2 — Bubblewrap",
            "Namespace container with GPU/audio passthrough",
        ),
        (
            "Tier 3 — Overlay",
            "Ephemeral RAM overlay, changes lost on exit",
        ),
    ];

    // Tier selection — radio buttons
    let tier_label = gtk::Label::new(Some("Isolation Tier:"));
    tier_label.set_xalign(0.0);
    tier_label.add_css_class("heading");
    vbox.append(&tier_label);

    let mut radio_buttons = Vec::new();
    let mut first_radio: Option<gtk::CheckButton> = None;

    for (i, (name, desc)) in tier_descriptions.iter().enumerate() {
        let hbox = gtk::Box::builder()
            .orientation(gtk::Orientation::Horizontal)
            .spacing(8)
            .build();

        let radio = if let Some(ref first) = first_radio {
            gtk::CheckButton::builder().group(first).build()
        } else {
            let r = gtk::CheckButton::new();
            first_radio = Some(r.clone());
            r
        };

        // Select the suggested tier
        if i == suggested_tier as usize {
            radio.set_active(true);
        }

        let label_text = format!("<b>{}</b> — {}", name, desc);
        let label = gtk::Label::new(None);
        label.set_markup(&label_text);
        label.set_xalign(0.0);

        hbox.append(&radio);
        hbox.append(&label);
        vbox.append(&hbox);
        radio_buttons.push(radio);
    }

    // Permission toggles
    let perm_label = gtk::Label::new(Some("Permissions:"));
    perm_label.set_xalign(0.0);
    perm_label.add_css_class("heading");
    vbox.append(&perm_label);

    let network_check = gtk::CheckButton::builder()
        .label("Allow network access (TAP bridge)")
        .active(false)
        .build();
    vbox.append(&network_check);

    let gpu_check = gtk::CheckButton::builder()
        .label("Allow GPU passthrough")
        .active(false)
        .build();
    vbox.append(&gpu_check);

    // Remember checkbox
    let remember_check = gtk::CheckButton::builder()
        .label("Remember these settings (save to rules.json)")
        .active(false)
        .build();
    vbox.append(&remember_check);

    // Button box
    let btn_box = gtk::Box::builder()
        .orientation(gtk::Orientation::Horizontal)
        .spacing(8)
        .halign(gtk::Align::End)
        .build();

    // Cancel button
    let cancel_btn = gtk::Button::builder().label("Cancel").build();
    {
        let result_ref = result.clone();
        let dialog_ref = dialog.clone();
        let suggested = suggested_tier;
        cancel_btn.connect_clicked(move |dialog_btn| {
            // On cancel, use suggested tier with no permissions
            if let Ok(mut r) = result_ref.lock() {
                *r = Some(TrustResult {
                    tier: suggested,
                    network: false,
                    gpu: false,
                    remember: false,
                });
            }
            // Close the parent window, not the button
            let _ = dialog_btn;
            dialog_ref.close();
        });
    }
    btn_box.append(&cancel_btn);

    // Apply button
    let apply_btn = gtk::Button::with_label("Apply & Run");
    apply_btn.add_css_class("suggested-action");
    {
        let result_ref = result.clone();
        let dialog_ref = dialog.clone();
        let radios = radio_buttons.clone();
        let network_ref = network_check.clone();
        let gpu_ref = gpu_check.clone();
        let remember_ref = remember_check.clone();
        apply_btn.connect_clicked(move |_| {
            // Determine which tier radio is active
            let mut selected_tier = suggested_tier;
            for (i, radio) in radios.iter().enumerate() {
                if radio.is_active() {
                    selected_tier = i as u8;
                    break;
                }
            }

            if let Ok(mut r) = result_ref.lock() {
                *r = Some(TrustResult {
                    tier: selected_tier,
                    network: network_ref.is_active(),
                    gpu: gpu_ref.is_active(),
                    remember: remember_ref.is_active(),
                });
            }
            dialog_ref.close();
        });
    }
    btn_box.append(&apply_btn);

    vbox.append(&btn_box);
    dialog.set_child(Some(&vbox));

    // Present and block until dialog closes
    dialog.present();

    let main_context = glib::MainContext::default();
    while dialog.is_visible() {
        main_context.iteration(true);
    }

    // Extract result
    let locked = result.lock();
    match locked {
        Ok(guard) => guard.clone().unwrap_or(TrustResult {
            tier: suggested_tier,
            network: false,
            gpu: false,
            remember: false,
        }),
        Err(_) => TrustResult {
            tier: suggested_tier,
            network: false,
            gpu: false,
            remember: false,
        },
    }
}
