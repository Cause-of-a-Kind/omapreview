import QtQuick
import Quickshell
import qs.Commons
import qs.Ui as Ui

Item {
    id: root
    readonly property string moduleName: "coak.omapreview"
    property var bar: null
    property var settings: ({})
    property bool interactive: true
    property bool pressable: true
    property bool concealed: false
    implicitWidth: button.implicitWidth
    implicitHeight: button.implicitHeight
    function launch() {
        Quickshell.execDetached([Quickshell.env("HOME") + "/.local/bin/omapreview"])
    }
    Ui.WidgetButton {
        id: button
        anchors.fill: parent
        bar: root.bar
        text: "󰈙"
        tooltipText: "Omapreview — fill and sign a PDF"
        horizontalMargin: 7.25
        verticalPadding: 8.75
        onPressed: function(buttonCode) { root.launch() }
    }
}
