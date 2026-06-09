/* Axon OS — Calamares installer slideshow
 * Shown while packages are being installed.
 * Requires Qt 5.15+ / QtQuick 2.15 (ships with Calamares on Ubuntu 22.04+).
 */

import QtQuick 2.15
import QtQuick.Controls 2.15
import Calamares.Slideshow 1.0

Presentation {
    id: presentation

    // -----------------------------------------------------------------------
    // Auto-advance every 6 seconds
    // -----------------------------------------------------------------------
    Timer {
        id:       slideTimer
        interval: 6000
        running:  presentation.activatedInCalamares
        repeat:   true
        onTriggered: presentation.goToNextSlide()
    }

    // -----------------------------------------------------------------------
    // Shared slide properties
    // -----------------------------------------------------------------------
    property color bgColor:      "#09090f"
    property color accentColor:  "#8b5cf6"
    property color textPrimary:  "#f1f5f9"
    property color textSecondary:"#94a3b8"
    property string fontFamily:  "Inter, sans-serif"

    // -----------------------------------------------------------------------
    // Slide 1 — Welcome to Axon OS
    // -----------------------------------------------------------------------
    Slide {
        Rectangle {
            anchors.fill: parent
            color: presentation.bgColor

            // Decorative gradient orb
            Rectangle {
                width: 320
                height: 320
                radius: 160
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.rightMargin: -80
                anchors.topMargin: -80
                color: "transparent"

                Rectangle {
                    anchors.fill: parent
                    radius: parent.radius
                    opacity: 0.15
                    gradient: Gradient {
                        GradientStop { position: 0.0; color: "#8b5cf6" }
                        GradientStop { position: 1.0; color: "#06b6d4" }
                    }
                }
            }

            Column {
                anchors.centerIn: parent
                spacing: 20

                // Accent bar
                Rectangle {
                    width: 48
                    height: 4
                    radius: 2
                    color: presentation.accentColor
                    anchors.horizontalCenter: parent.horizontalCenter
                }

                Text {
                    text: "Welcome to Axon OS"
                    font.family: presentation.fontFamily
                    font.pixelSize: 38
                    font.weight: Font.Bold
                    color: presentation.textPrimary
                    anchors.horizontalCenter: parent.horizontalCenter
                }

                Text {
                    text: "A privacy-first, AI-native Linux desktop"
                    font.family: presentation.fontFamily
                    font.pixelSize: 16
                    color: presentation.textSecondary
                    anchors.horizontalCenter: parent.horizontalCenter
                }

                Text {
                    text: "Your system is being installed.\nSit back while we set everything up."
                    font.family: presentation.fontFamily
                    font.pixelSize: 14
                    color: presentation.textSecondary
                    horizontalAlignment: Text.AlignHCenter
                    anchors.horizontalCenter: parent.horizontalCenter
                    lineHeight: 1.5
                }
            }
        }
    }

    // -----------------------------------------------------------------------
    // Slide 2 — AI-Powered Desktop
    // -----------------------------------------------------------------------
    Slide {
        Rectangle {
            anchors.fill: parent
            color: presentation.bgColor

            // Background grid lines (subtle)
            Canvas {
                anchors.fill: parent
                opacity: 0.04
                onPaint: {
                    var ctx = getContext("2d")
                    ctx.strokeStyle = "#8b5cf6"
                    ctx.lineWidth = 1
                    for (var x = 0; x < width; x += 40) {
                        ctx.beginPath()
                        ctx.moveTo(x, 0)
                        ctx.lineTo(x, height)
                        ctx.stroke()
                    }
                    for (var y = 0; y < height; y += 40) {
                        ctx.beginPath()
                        ctx.moveTo(0, y)
                        ctx.lineTo(width, y)
                        ctx.stroke()
                    }
                }
            }

            Column {
                anchors.centerIn: parent
                spacing: 24

                Rectangle {
                    width: 72
                    height: 72
                    radius: 18
                    color: "#1a1a2e"
                    border.color: presentation.accentColor
                    border.width: 2
                    anchors.horizontalCenter: parent.horizontalCenter

                    Text {
                        anchors.centerIn: parent
                        text: "AI"
                        font.family: presentation.fontFamily
                        font.pixelSize: 26
                        font.weight: Font.Bold
                        color: presentation.accentColor
                    }
                }

                Text {
                    text: "AI-Powered Desktop"
                    font.family: presentation.fontFamily
                    font.pixelSize: 34
                    font.weight: Font.Bold
                    color: presentation.textPrimary
                    anchors.horizontalCenter: parent.horizontalCenter
                }

                Column {
                    spacing: 12
                    anchors.horizontalCenter: parent.horizontalCenter

                    Repeater {
                        model: [
                            "Intent Bar — type what you want, AI does the rest",
                            "Axon AI Panel — context-aware assistant always at hand",
                            "Ollama integration — powerful local models, no cloud needed"
                        ]

                        Row {
                            spacing: 10
                            anchors.horizontalCenter: parent.horizontalCenter

                            Rectangle {
                                width: 6
                                height: 6
                                radius: 3
                                color: presentation.accentColor
                                anchors.verticalCenter: parent.verticalCenter
                            }

                            Text {
                                text: modelData
                                font.family: presentation.fontFamily
                                font.pixelSize: 14
                                color: presentation.textSecondary
                            }
                        }
                    }
                }
            }
        }
    }

    // -----------------------------------------------------------------------
    // Slide 3 — Zero Cloud Required
    // -----------------------------------------------------------------------
    Slide {
        Rectangle {
            anchors.fill: parent
            color: presentation.bgColor

            // Bottom gradient
            Rectangle {
                anchors.bottom: parent.bottom
                anchors.left: parent.left
                anchors.right: parent.right
                height: parent.height * 0.4
                opacity: 0.12
                gradient: Gradient {
                    orientation: Gradient.Vertical
                    GradientStop { position: 0.0; color: "transparent" }
                    GradientStop { position: 1.0; color: "#8b5cf6" }
                }
            }

            Column {
                anchors.centerIn: parent
                spacing: 20

                Text {
                    text: "Zero Cloud Required"
                    font.family: presentation.fontFamily
                    font.pixelSize: 36
                    font.weight: Font.Bold
                    color: presentation.textPrimary
                    anchors.horizontalCenter: parent.horizontalCenter
                }

                Text {
                    text: "Everything runs on your hardware.\nYour data stays yours."
                    font.family: presentation.fontFamily
                    font.pixelSize: 16
                    color: presentation.textSecondary
                    horizontalAlignment: Text.AlignHCenter
                    anchors.horizontalCenter: parent.horizontalCenter
                    lineHeight: 1.6
                }

                // Feature tags
                Row {
                    spacing: 12
                    anchors.horizontalCenter: parent.horizontalCenter

                    Repeater {
                        model: ["Private", "Offline-first", "Open Source"]

                        Rectangle {
                            height: 32
                            width: tagLabel.implicitWidth + 24
                            radius: 16
                            color: "#1a1a2e"
                            border.color: presentation.accentColor
                            border.width: 1

                            Text {
                                id: tagLabel
                                anchors.centerIn: parent
                                text: modelData
                                font.family: presentation.fontFamily
                                font.pixelSize: 13
                                color: presentation.accentColor
                            }
                        }
                    }
                }

                // Divider
                Rectangle {
                    width: 200
                    height: 1
                    color: "#2d2d44"
                    anchors.horizontalCenter: parent.horizontalCenter
                }

                Text {
                    text: "Installation almost complete..."
                    font.family: presentation.fontFamily
                    font.pixelSize: 13
                    color: presentation.textSecondary
                    anchors.horizontalCenter: parent.horizontalCenter
                    opacity: 0.7
                }
            }
        }
    }
}
