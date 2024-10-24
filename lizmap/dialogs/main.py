__copyright__ = 'Copyright 2023, 3Liz'
__license__ = 'GPL version 3'
__email__ = 'info@3liz.org'

import logging

from pathlib import Path
from typing import Optional

from qgis.core import Qgis, QgsApplication, QgsProject, QgsSettings
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon, QImageReader, QPixmap
from qgis.PyQt.QtWidgets import (
    QDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
)
from qgis.utils import iface

try:
    from qgis.PyQt.QtWebKitWidgets import QWebView
    WEBKIT_AVAILABLE = True
except ModuleNotFoundError:
    WEBKIT_AVAILABLE = False

from lizmap.definitions.definitions import LwcVersions, ServerComboData
from lizmap.qgis_plugin_tools.tools.i18n import tr
from lizmap.qgis_plugin_tools.tools.resources import load_ui, resources_path
from lizmap.qt_style_sheets import COMPLETE_STYLE_SHEET
from lizmap.tools import format_qgis_version, human_size, qgis_version

FORM_CLASS = load_ui('ui_lizmap.ui')
LOGGER = logging.getLogger("Lizmap")


class LizmapDialog(QDialog, FORM_CLASS):
    def __init__(self, parent=None):
        """Constructor."""
        super().__init__(parent)
        self.setupUi(self)
        self.project = QgsProject.instance()

        self.label_lizmap_logo.setText('')
        pixmap = QPixmap(resources_path('icons', 'logo.png'))
        # noinspection PyUnresolvedReferences
        pixmap = pixmap.scaled(100, 100, Qt.KeepAspectRatio)
        self.label_lizmap_logo.setPixmap(pixmap)

        if WEBKIT_AVAILABLE:
            self.dataviz_viewer = QWebView()
        else:
            self.dataviz_viewer = QLabel(tr('You must install Qt Webkit to enable this feature.'))
        self.html_content.layout().addWidget(self.dataviz_viewer)

        if qgis_version() >= 31400:
            from qgis.gui import QgsFeaturePickerWidget
            self.dataviz_feature_picker = QgsFeaturePickerWidget()
        else:
            self.dataviz_feature_picker = QLabel(tr("You must install QGIS 3.16 to enable the dataviz preview."))

        self.feature_picker_layout.addWidget(self.dataviz_feature_picker)
        self.feature_picker_layout.addItem(QSpacerItem(20, 40, QSizePolicy.Minimum, QSizePolicy.Expanding))

        # IGN and google
        self.inIgnKey.textChanged.connect(self.check_ign_french_free_key)
        self.inIgnKey.textChanged.connect(self.check_api_key_address)
        self.inGoogleKey.textChanged.connect(self.check_api_key_address)

        # Layer tree
        self.layer_tree.headerItem().setText(0, tr('List of layers'))

        self.check_project_thumbnail()
        self.setup_icons()

    def check_api_key_address(self):
        """ Check the API key is provided for the address search bar. """
        provider = self.liExternalSearch.currentData()
        if provider in ('google', 'ign'):
            if provider == 'google':
                key = self.inGoogleKey.text()
            else:
                provider = 'IGN'
                key = self.inIgnKey.text()

            if not key:
                QMessageBox.critical(
                    self,
                    tr('Address provider'),
                    tr('You have selected "{}" for the address search bar.').format(provider)
                    + "\n\n"
                    + tr(
                        'However, you have not provided any API key for this provider. Please add one in the '
                        '"Basemaps" panel to use this provider.'
                    ),
                    QMessageBox.Ok
                )

    def block_signals_address(self, flag: bool):
        """Block or not signals when reading the CFG to avoid the message box."""
        # https://github.com/3liz/lizmap-plugin/issues/477
        # When reading the CFG file, the address provider is set, before the key field is filled.
        # The signal is too early
        self.inIgnKey.blockSignals(flag)
        self.inGoogleKey.blockSignals(flag)
        self.liExternalSearch.blockSignals(flag)

    def check_ign_french_free_key(self):
        """ French IGN free API keys choisirgeoportail/pratique do not include all layers. """
        key = self.inIgnKey.text()
        if not key:
            self.cbIgnTerrain.setEnabled(False)
            self.cbIgnTerrain.setChecked(False)
        else:
            self.cbIgnTerrain.setEnabled(True)

    def check_qgis_version(self):
        """ Compare QGIS desktop and server versions. """
        current = format_qgis_version(qgis_version())
        qgis_desktop = (current[0], current[1])

        metadata = self.current_server_info(ServerComboData.JsonMetadata.value)
        try:
            qgis_server = metadata.get('qgis_server_info').get('metadata').get('version').split('.')
            qgis_server = (int(qgis_server[0]), int(qgis_server[1]))
        except AttributeError:
            # Maybe returning LWC 3.4 or LWC 3.5 without server plugin
            return

        if qgis_server < qgis_desktop:
            title = tr('QGIS server version is lower than QGIS desktop version')
            description = tr('Your QGIS desktop is writing QGS project in the future compare to QGIS server.')
            more = tr('Current QGIS server selected : ')
            more += '<b>{}.{}</b>'.format(qgis_server[0], qgis_server[1])
            more += "<br>"
            more += tr('Current QGIS desktop : ')
            more += '<b>{}.{}</b>'.format(qgis_desktop[0], qgis_desktop[1])
            more += "<br><br>"
            more += tr('Your QGIS desktop is writing QGS project in the future compare to QGIS server.')
            more += "<br>"
            more += tr(
                    'You are strongly encouraged to upgrade your QGIS server. You will have issues when your QGIS '
                    'server will read your QGS project made with this version of QGIS desktop.'
            )
            LOGGER.error(title)
            self.display_message_bar(title, description, Qgis.Warning, more_details=more)

    def current_server_info(self, info: ServerComboData):
        """ Return the current LWC server information from the server combobox. """
        return self.server_combo.currentData(info)

    def current_lwc_version(self) -> Optional[LwcVersions]:
        """ Return the current LWC version from the server combobox. """
        return self.metadata_to_lwc_version(self.current_server_info(ServerComboData.JsonMetadata.value))

    @classmethod
    def metadata_to_lwc_version(cls, metadata: dict) -> Optional[LwcVersions]:
        """ Check in a metadata for the LWC version."""
        if not metadata:
            # When the server is not reachable
            return None
        return LwcVersions.find(metadata['info']['version'])

    def current_repository(self) -> str:
        """ Fetch the current directory on the server if available. """
        if not self.repository_combo.isVisible():
            return ''

        return self.repository_combo.currentData()

    def tooltip_server_combo(self, index: int):
        """ Set the tooltip for a given row in the server combo. """
        # This method must use itemData() as we are not the current selected server in the combobox.
        url = self.server_combo.itemData(index, ServerComboData.ServerUrl.value)
        metadata = self.server_combo.itemData(index, ServerComboData.JsonMetadata.value)
        target_version = self.metadata_to_lwc_version(metadata)
        if target_version:
            target_version = target_version.value
        else:
            target_version = tr('Unknown')

        msg = tr(
            '<b>URL</b> {}<br>'
            '<b>Target branch</b> {}'
        ).format(url, target_version)
        self.server_combo.setItemData(index, msg, Qt.ToolTipRole)

    def refresh_combo_repositories(self):
        """ Refresh the combobox about repositories. """
        # Set the default error message that could happen for the dataviz
        # TODO change to latest 3.6.X in a few months
        error = tr(
            "Your current version of the selected server doesn't support the plot preview. "
            "You must upgrade at least to Lizmap Web Client "
            "<a href=\"https://github.com/3liz/lizmap-web-client/releases/tag/3.6.1\">3.6.1</a>."
            "\n\n"
            "Upgrade to the latest 3.6.X available."
        )
        self.dataviz_error_message.setText(error)

        self.repository_combo.clear()

        current = self.current_server_info(ServerComboData.ServerUrl.value)
        if not current:
            return

        if not current.endswith('/'):
            current += '/'

        metadata = self.current_server_info(ServerComboData.JsonMetadata.value)
        if not metadata:
            self.repository_combo.setVisible(False)
            self.stacked_dataviz_preview.setCurrentWidget(self.error_content)
            return

        repositories = metadata.get("repositories")
        if repositories is None:
            self.repository_combo.setVisible(False)
            self.stacked_dataviz_preview.setCurrentWidget(self.error_content)
            return

        # At this stage, a more precise error message for the dataviz
        error = tr("You should select a plot to have the preview.")
        self.dataviz_error_message.setText(error)

        self.repository_combo.setVisible(True)
        self.stacked_dataviz_preview.setCurrentWidget(self.error_content)

        if len(repositories) == 0:
            # The list might be empty on the server
            self.repository_combo.setToolTip("There isn't repository on the server")
            return

        self.repository_combo.setToolTip("List of repositories found on the server")

        for repository_id, repository_data in repositories.items():
            self.repository_combo.addItem(repository_data['label'], repository_id)
            index = self.repository_combo.findData(repository_id)
            self.repository_combo.setItemData(index, repository_id, Qt.ToolTipRole)

        # Restore the previous value if possible
        previous = self.project.customVariables().get('lizmap_repository')
        if not previous:
            return

        index = self.repository_combo.findData(previous)
        if not index:
            return

        self.repository_combo.setCurrentIndex(index)

    def display_message_bar(
            self,
            title: str,
            message: str = None,
            level: Qgis.MessageLevel = Qgis.Info,
            duration: int = None,
            more_details: str = None,
            open_logs: bool = False):
        """Display a message.

        :param title: Title of the message.
        :type title: basestring

        :param message: The message.
        :type message: basestring

        :param level: A QGIS error level.

        :param duration: Duration in second.
        :type duration: int

        :param open_logs: If we need to add a button for the log panel.
        :type open_logs: bool

        :param more_details: The message to display in the "More button".
        :type more_details: basestring
        """
        widget = self.message_bar.createMessage(title, message)

        if more_details or open_logs:
            # Adding the button
            button = QPushButton(widget)
            widget.layout().addWidget(button)

            if open_logs:
                button.setText(tr('Open log panel'))
                # noinspection PyUnresolvedReferences
                button.pressed.connect(
                    lambda: iface.openMessageLog())
            else:
                button.setText(tr('More details'))
                # noinspection PyUnresolvedReferences
                button.pressed.connect(
                    lambda: QMessageBox.information(None, title, more_details))

        if duration is None:
            duration = int(QgsSettings().value("qgis/messageTimeout", 5))

        self.message_bar.pushWidget(widget, level, duration)

    def setup_icons(self):
        """ Setup icons in the left menu. """
        i = 0

        # Information
        icon = QIcon()
        icon.addFile(resources_path('icons', '03-metadata-white'), mode=QIcon.Normal)
        icon.addFile(resources_path('icons', '03-metadata-dark'), mode=QIcon.Selected)
        self.mOptionsListWidget.item(i).setIcon(icon)
        i += 1

        # Map options
        icon = QIcon()
        icon.addFile(resources_path('icons', '15-baselayer-white.png'), mode=QIcon.Normal)
        icon.addFile(resources_path('icons', '15-baselayer-dark.png'), mode=QIcon.Selected)
        self.mOptionsListWidget.item(i).setIcon(icon)
        i += 1

        # Layers
        icon = QIcon()
        icon.addFile(resources_path('icons', '02-switcher-white.png'), mode=QIcon.Normal)
        icon.addFile(resources_path('icons', '02-switcher-dark.png'), mode=QIcon.Selected)
        self.mOptionsListWidget.item(i).setIcon(icon)
        i += 1

        # Base layer
        icon = QIcon()
        icon.addFile(resources_path('icons', '02-switcher-white.png'), mode=QIcon.Normal)
        icon.addFile(resources_path('icons', '02-switcher-dark.png'), mode=QIcon.Selected)
        self.mOptionsListWidget.item(i).setIcon(icon)
        i += 1

        # Layouts
        icon = QIcon()
        icon.addFile(resources_path('icons', '08-print-white.png'), mode=QIcon.Normal)
        icon.addFile(resources_path('icons', '08-print-dark.png'), mode=QIcon.Selected)
        self.mOptionsListWidget.item(i).setIcon(icon)
        i += 1

        # Locate by layer
        icon = QIcon()
        icon.addFile(resources_path('icons', '04-locate-white.png'), mode=QIcon.Normal)
        icon.addFile(resources_path('icons', '04-locate-dark.png'), mode=QIcon.Selected)
        self.mOptionsListWidget.item(i).setIcon(icon)
        i += 1

        # Attribute table
        icon = QIcon()
        icon.addFile(resources_path('icons', '11-attribute-white.png'), mode=QIcon.Normal)
        icon.addFile(resources_path('icons', '11-attribute-dark.png'), mode=QIcon.Selected)
        self.mOptionsListWidget.item(i).setIcon(icon)
        i += 1

        # Layer editing
        icon = QIcon()
        icon.addFile(resources_path('icons', '10-edition-white.png'), mode=QIcon.Normal)
        icon.addFile(resources_path('icons', '10-edition-dark.png'), mode=QIcon.Selected)
        self.mOptionsListWidget.item(i).setIcon(icon)
        i += 1

        # Tooltip layer
        icon = QIcon()
        icon.addFile(resources_path('icons', '16-tooltip-white.png'), mode=QIcon.Normal)
        icon.addFile(resources_path('icons', '16-tooltip-dark.png'), mode=QIcon.Selected)
        self.mOptionsListWidget.item(i).setIcon(icon)
        i += 1

        # Filter data with form
        icon = QIcon()
        icon.addFile(resources_path('icons', 'filter-icon-white.png'), mode=QIcon.Normal)
        icon.addFile(resources_path('icons', 'filter-icon-dark.png'), mode=QIcon.Selected)
        self.mOptionsListWidget.item(i).setIcon(icon)
        i += 1

        # Filter layer by user
        icon = QIcon()
        icon.addFile(resources_path('icons', '12-user-white.png'), mode=QIcon.Normal)
        icon.addFile(resources_path('icons', '12-user-dark.png'), mode=QIcon.Selected)
        self.mOptionsListWidget.item(i).setIcon(icon)
        i += 1

        # Dataviz
        icon = QIcon()
        icon.addFile(resources_path('icons', 'dataviz-icon-white.png'), mode=QIcon.Normal)
        icon.addFile(resources_path('icons', 'dataviz-icon-dark.png'), mode=QIcon.Selected)
        self.mOptionsListWidget.item(i).setIcon(icon)
        i += 1

        # Time manager
        icon = QIcon()
        icon.addFile(resources_path('icons', '13-timemanager-white.png'), mode=QIcon.Normal)
        icon.addFile(resources_path('icons', '13-timemanager-dark.png'), mode=QIcon.Selected)
        self.mOptionsListWidget.item(i).setIcon(icon)
        i += 1

        # Atlas
        icon = QIcon()
        icon.addFile(resources_path('icons', 'atlas-icon-white.png'), mode=QIcon.Normal)
        icon.addFile(resources_path('icons', 'atlas-icon-dark.png'), mode=QIcon.Selected)
        self.mOptionsListWidget.item(i).setIcon(icon)
        i += 1

        # Log
        # noinspection PyCallByClass,PyArgumentList
        icon = QIcon(QgsApplication.iconPath('mMessageLog.svg'))
        self.mOptionsListWidget.item(i).setIcon(icon)
        i += 1

        # Set stylesheet for QGroupBox
        self.gb_tree.setStyleSheet(COMPLETE_STYLE_SHEET)
        self.gb_layerSettings.setStyleSheet(COMPLETE_STYLE_SHEET)
        self.gb_ftp.setStyleSheet(COMPLETE_STYLE_SHEET)
        self.gb_project_thumbnail.setStyleSheet(COMPLETE_STYLE_SHEET)
        self.gb_visibleTools.setStyleSheet(COMPLETE_STYLE_SHEET)
        self.gb_Scales.setStyleSheet(COMPLETE_STYLE_SHEET)
        self.gb_extent.setStyleSheet(COMPLETE_STYLE_SHEET)
        self.gb_externalLayers.setStyleSheet(COMPLETE_STYLE_SHEET)
        self.gb_lizmapExternalBaselayers.setStyleSheet(COMPLETE_STYLE_SHEET)
        self.gb_generalOptions.setStyleSheet(COMPLETE_STYLE_SHEET)
        self.gb_interface.setStyleSheet(COMPLETE_STYLE_SHEET)
        self.gb_baselayersOptions.setStyleSheet(COMPLETE_STYLE_SHEET)

    def check_project_thumbnail(self):
        """ Check the project thumbnail and display the metadata. """
        # Check the project image
        # https://github.com/3liz/lizmap-web-client/blob/master/lizmap/modules/view/controllers/media.classic.php
        # Line 251
        images_types = ['jpg', 'jpeg', 'png', 'gif', 'webp', 'avif']
        images_types.extend([f.upper() for f in images_types])
        tooltip = tr(
            "You can add a file named {}.qgs.EXTENSION with one of the following extension : jpg, jpeg, png, gif."
        ).format(self.project.baseName())
        tooltip += '\n'
        tooltip += tr('Lizmap Web Client 3.6 adds webp and avif formats.')
        tooltip += '\n'
        tooltip += tr('The width and height should be maximum 250x250px.')
        tooltip += tr('The size should be ideally less than 50 KB for JPG, 150KB for PNG.')
        tooltip += '\n'
        tooltip += tr('You can use this online tool to optimize the size of the picture :')
        tooltip += ' https://squoosh.app'
        self.label_project_thumbnail.setToolTip(tooltip)
        self.label_project_thumbnail.setOpenExternalLinks(True)

        self.label_project_thumbnail.setText(tr("No thumbnail detected."))

        if self.check_cfg_file_exists():
            for test_file in images_types:
                thumbnail = Path(f'{self.project.fileName()}.{test_file}')
                if thumbnail.exists():
                    image_size = QImageReader(str(thumbnail)).size()
                    self.label_project_thumbnail.setText(
                        tr("Thumbnail detected, {}x{}px, {}").format(
                            image_size.width(),
                            image_size.height(),
                            human_size(thumbnail.stat().st_size))
                    )
                    break

    def cfg_file(self) -> Path:
        """ Return the path to the current CFG file. """
        return Path(self.project.fileName() + '.cfg')

    def check_cfg_file_exists(self) -> bool:
        """ Return boolean if a CFG file exists for the given project. """
        return self.cfg_file().exists()

    def allow_navigation(self, allow_navigation: bool, message: str = ''):
        """ Allow the navigation or not in the UI. """
        for i in range(1, self.mOptionsListWidget.count()):
            item = self.mOptionsListWidget.item(i)
            if allow_navigation:
                item.setFlags(item.flags() | Qt.ItemIsEnabled)
            else:
                item.setFlags(item.flags() & ~ Qt.ItemIsEnabled)

        if allow_navigation:
            self.project_valid.setVisible(False)
            self.label_warning_project.setText('')
        else:
            self.project_valid.setVisible(True)
            self.label_warning_project.setText(message)

    def activateWindow(self):
        """ When the dialog displayed, to trigger functions in the plugin when the dialog is opening. """
        self.check_project_thumbnail()
        LOGGER.info("Opening the Lizmap dialog.")
        super().activateWindow()
