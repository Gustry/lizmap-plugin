""" Table manager for dataviz. """
import json
import logging

from typing import Optional, Type

from qgis.core import (
    QgsApplication,
    QgsAuthMethodConfig,
    QgsBlockingNetworkRequest,
    QgsProject,
    QgsSettings,
)
from qgis.PyQt.QtCore import (
    QByteArray,
    QJsonDocument,
    QLocale,
    QUrl,
    QUrlQuery,
)
from qgis.PyQt.QtNetwork import QNetworkRequest
from qgis.PyQt.QtWidgets import QDialog

from lizmap.definitions.base import BaseDefinitions
from lizmap.definitions.definitions import ServerComboData
from lizmap.qgis_plugin_tools.tools.i18n import tr
from lizmap.qgis_plugin_tools.tools.resources import (
    plugin_name,
    resources_path,
)
from lizmap.table_manager.base import TableManager
from lizmap.tools import to_bool

LOGGER = logging.getLogger(plugin_name())


__copyright__ = 'Copyright 2023, 3Liz'
__license__ = 'GPL version 3'
__email__ = 'info@3liz.org'


class TableManagerDataviz(TableManager):

    """ Table manager for dataviz.

    Note, this subclass is coming way later than the actual implementation of TableManager.
    There are a lot of lines of code in TableManager related to only the dataviz.
    """

    def __init__(
            self, parent, definitions: BaseDefinitions, edition: Type[QDialog], table, edit_button, up_button,
            down_button):
        TableManager.__init__(self, parent, definitions, edition, table, None, edit_button, up_button, down_button)

        label = tr(
            "This plot is a preview, using the <b>data</b> and the <b>project</b> currently stored "
            "<b>on the server</b>, but using your <b>current</b> configuration for the given plot."
        )
        self.parent.label_helper_dataviz.setText(label)

        self.table.itemSelectionChanged.connect(self.preview_dataviz_dialog)
        self.parent.dataviz_feature_picker.setShowBrowserButtons(True)
        self.parent.dataviz_feature_picker.featureChanged.connect(self.preview_dataviz_dialog)

    def preview_dataviz_dialog(self):
        """ Open a new dialog with a preview of the dataviz. """
        # Always display the text by default
        self.parent.stacked_dataviz_preview.setCurrentIndex(1)
        self.parent.dataviz_feature_picker.setVisible(False)

        selection = self.table.selectedIndexes()
        if len(selection) <= 0:
            return

        if not self.parent.repository_combo.isVisible():
            return

        # The check before is not enough if we just have changed the server while we are in the dataviz panel.
        metadata = self.parent.server_combo.currentData(ServerComboData.JsonMetadata.value)
        if not metadata:
            return

        if not metadata.get("repositories"):
            return

        data = self.to_json()
        row = str(selection[0].row())
        plot_config = data[row]

        server = self.parent.server_combo.currentData(ServerComboData.ServerUrl.value)
        auth_id = self.parent.server_combo.currentData(ServerComboData.AuthId.value)
        if not server or not auth_id:
            return

        repository = self.parent.repository_combo.currentData()
        repository_label = self.parent.repository_combo.currentText()
        project = QgsProject.instance().baseName()

        json_data = {
            "repository": repository,
            "project": project,
            "plot_config": plot_config,
        }
        if to_bool(plot_config.get('popup_display_child_plot', False)):
            self.parent.dataviz_feature_picker.setAllowNull(not to_bool(plot_config.get('only_show_child', False)))
            expression_filter = self.dataviz_expression_filter(plot_config['layerId'])
            if expression_filter:
                json_data['exp_filter'] = expression_filter

        json_object = json.dumps(json_data, indent=4)

        url = QUrl('{}index.php/dataviz/service/'.format(server))

        conf = QgsAuthMethodConfig()
        QgsApplication.authManager().loadAuthenticationConfig(auth_id, conf, True)
        if not conf.id():
            # TODO, should be removed soon, because we should force migrate existing servers.
            # PR https://github.com/3liz/lizmap-plugin/pull/449
            error = tr(
                'You must fill authentification for the given server on the left panel. Go back in the first '
                'panel of the plugin and fill the login/password for the server.'
            )
            self.parent.dataviz_error_message.setText(error)
            return

        # Until now, we are all good to make the HTTP request, let's display the GIF
        html_content = "<body><center><img src=\"{}\"></center><body>".format(resources_path('icons/loading.gif'))
        base_url = QUrl.fromLocalFile(resources_path('images', 'non_existing_file.png'))
        self.parent.dataviz_viewer.setHtml(html_content, base_url)
        self.parent.stacked_dataviz_preview.setCurrentIndex(0)

        locale = QgsSettings().value("locale/userLocale", QLocale().name())[0:2]

        params = QUrlQuery()
        params.addQueryItem("lang", locale)

        url = QUrl(url)
        url.setQuery(params)
        network_request = QNetworkRequest()
        network_request.setRawHeader(b"Content-Type", b"application/json")
        network_request.setRawHeader(b"Accept", b"application/json")
        network_request.setUrl(url)

        request = QgsBlockingNetworkRequest()
        request.setAuthCfg(auth_id)

        doc = QJsonDocument.fromJson(json_object.encode('utf8'))
        error = request.post(network_request, QByteArray(doc.toJson()))

        if error != QgsBlockingNetworkRequest.NoError:
            if error == QgsBlockingNetworkRequest.NetworkError:
                message = tr('Network error : {}').format(server)
            elif error == QgsBlockingNetworkRequest.TimeoutError:
                message = tr('Timeout error : {}').format(server)
            elif error == QgsBlockingNetworkRequest.ServerExceptionError:
                # Customized error from the server about the request
                # We should have a JSON
                response = request.reply().content()
                json_response = json.loads(response.data().decode('utf-8'))
                errors = json_response.get('errors')
                # Message from the server
                message = '<b>{}</b><br><br>{}'.format(errors.get('title'), errors.get('detail'))
                # Let's add some more context to help
                message += '<br><br>' + tr("Given context for the request") + ' : <br>'
                message += '<b>' + tr('Server') + '</b> : ' + server + '<br>'
                message += (
                        '<b>' + tr('Repository') + '</b> : ' + repository
                        + ', <b>' + tr('alias') + '</b> : ' + repository_label
                )
                message += '<br>'
                message += '<b>' + tr('Project') + '</b> : ' + project + '.qgs'
            else:
                message = tr("Unknown error : code {}").format(error)

            self.parent.dataviz_error_message.setText(message)
            self.parent.stacked_dataviz_preview.setCurrentIndex(1)
            return

        response = request.reply().content()
        json_response = json.loads(response.data().decode('utf-8'))

        with open(resources_path('html', 'dataviz.html'), encoding='utf8') as f:
            html_template = f.read()

        if json_response.get('errors'):
            # Looks like we are on LWC < 3.6.1
            # Shouldn't happen as well because of a previous check
            self.parent.dataviz_error_message.setText(json_response.get('errors').get('title', 'Unknown error'))
            self.parent.stacked_dataviz_preview.setCurrentIndex(1)
            return

        if not json_response.get('data'):
            # Shouldn't happen ...
            self.parent.dataviz_error_message.setText("Unknown error")
            self.parent.stacked_dataviz_preview.setCurrentIndex(1)
            return

        html_content = html_template.format(
            plot_data=json.dumps(json_response['data']),
            plot_layout=json.dumps(json_response['layout']),
            plot_config=json.dumps({
                "showLink": False,
                "scrollZoom": False,
                "locale": locale,
                "responsive": True,
            }),
            plotly=server + json_response['plotly']['script'],
            locale=server + json_response['plotly']['locale'],
        )
        base_url = QUrl.fromLocalFile(resources_path('images', 'non_existing_file.png'))
        self.parent.dataviz_viewer.setHtml(html_content, base_url)

        # Only when we are all good
        self.parent.stacked_dataviz_preview.setCurrentIndex(0)

    def dataviz_expression_filter(self, layer_id: str) -> Optional[str]:
        """ Return the expression filter if possible. """
        project = QgsProject.instance()
        layer = project.mapLayer(layer_id)
        if not layer:
            return

        relations = project.relationManager().referencingRelations(layer)
        if not relations:
            return

        if len(relations) >= 2:
            LOGGER.warning(
                "Many relations has been found for the dataviz preview with the layer ID '{}'. "
                "Only the first one is used.".format(layer_id)
            )

        parent_layer = relations[0].referencingLayer()
        child_layer = relations[0].referencedLayer()
        field = relations[0].referencingFields()

        # We use only the first field.
        field = parent_layer.fields().at(field[0])

        # Set the layer in the feature combobox if not set or if it's a different one
        previous_layer = self.parent.dataviz_feature_picker.layer()
        if previous_layer and previous_layer.id() != layer.id() or not previous_layer:
            self.parent.dataviz_feature_picker.setLayer(child_layer)

        # Make widget visible
        self.parent.dataviz_feature_picker.setVisible(True)

        feature = self.parent.dataviz_feature_picker.feature()
        if feature.isValid():
            # The current feature can be set to NULL because of "only_show_child"
            return "\"{}\" IN ('{}')".format(field.name(), feature.id())