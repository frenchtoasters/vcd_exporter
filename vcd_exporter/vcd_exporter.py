#!/usr/bin/env python
# -*- python -*-
# -*- coding: utf-8 -*-

import datetime
import pytz
import yaml
import textwrap
from argparse import ArgumentParser

# Twisted
from twisted.internet import reactor, endpoints, defer  # , threads # Leaves room for improvement
from twisted.web.server import Site, NOT_DONE_YET
from twisted.web.resource import Resource

# Prometheus
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import CollectorRegistry
from prometheus_client.exposition import generate_latest

# vCD
from pyvcloud.vcd.client import *
from pyvcloud.vcd.org import *
from pyvcloud.vcd.vapp import *

# Ignore SSL warnings
requests.packages.urllib3.disable_warnings()


def log(data, *args):
    """
    Log any message in a uniform format
    """
    print("[{0}] {1}".format(datetime.utcnow().replace(tzinfo=pytz.utc), data % args))


class ListCollector(object):
    """
    Class for returning full list of collected metrics
    """

    def __init__(self, metrics):
        self.metrics = list(metrics)

    def collect(self):
        return self.metrics


class VcdApplicationResource(Resource):
    """
    Class for Vac Exporter Application configuration
    """

    def __init__(self, args):
        Resource.__init__(self)
        self.config = {}
        self.section = ''
        self.args = args

    def configure(self, section):
        """
        Configure connection to Vac
        :param section: section in the config file
        """
        if self.args.config_file:
            with open(self.args.config_file) as handle:
                self.config = yaml.safe_load(handle)
                try:
                    self.config.get(section)
                except Exception as err:
                    log("Unable to find section: {}".format(err))
                    return defer.logError(err)

        else:
            """
            Set configuration from OS environement variables
            """
            self.config = {
                'default': {
                    'vcd_user': os.environ.get('VCD_USER'),
                    'vcd_org': os.environ.get('VCD_ORG'),
                    'vcd_password': os.environ.get('VCD_PASSWORD'),
                    'ignore_ssl': os.environ.get('VCD_IGNORE_SSL', False),
                }
            }
            for key in os.environ.keys():
                if key == 'VCD_USER':
                    continue
                if not key.startswith('VCD_') or not key.endswith('_USER'):
                    continue

                section = key.split('_', 1)[1].rsplit('_', 1)[0]

                self.config[section.lower()] = {
                    'vcd_user': os.environ.get('VCD_{}_USER'.format(section)),
                    'vcd_org': os.environ.get('VCD_{}_ORG'.format(section)),
                    'vcd_password': os.environ.get('VCD_{}_PASSWORD'.format(section)),
                    'ignore_ssl': os.environ.get('VCD_{}_IGNORE_SSL'.format(section), False),
                }

        # Set section for context
        if section == 'default':
            self.section = 'default'
        else:
            try:
                if self.config.get(section):
                    self.section = section
                else:
                    raise Exception("Unable to find section: {}".format(section))
            except Exception as err:
                log("Section not valid, error: {}".format(err))
                return 2

        # Open vCD User context
        with VcdConnection(self.config[self.section].get('vcd_user'), self.config[self.section].get('vcd_org'),
                           self.config[self.section].get('vcd_password'), self.config[self.section].get('vcd_host'),
                           self.config[self.section].get('ignore_ssl')
                           ) as vcd:
            conn = vcd.connection()

            def onError(err):
                log("Error connecting to vCD endpoint: {}".format(err))
                pass

            conn.addErrback(onError)

            def onSuccess(connection):
                self.vcd_user = self.config[self.section].get('vcd_user')
                self.vcd_org = self.config[self.section].get('vcd_org')
                self.vcd_password = self.config[self.section].get('vcd_password')
                self.vcd_host = self.config[self.section].get('vcd_host')
                self.ignore_ssl = self.config[self.section].get('ignore_ssl')
                self.vcd_client = connection
                log("Configuration complete for: {}".format(self.config[self.section].get('vcd_host')))

            conn.addCallback(onSuccess)

        return NOT_DONE_YET

    def render_GET(self, request):
        """
        Render data from collector
        """
        if b'target' in request.args:
            result = self.configure(request.args[b'target'][0].decode("utf-8"))
            if result == 2:
                return "No Config found for: {}".format(request.args[b'target'][0].decode("utf-8")).encode()
            else:
                collector = VcdCollector(
                    request.args[b'target'][0].decode("utf-8"),
                    self.vcd_user,
                    self.vcd_org,
                    self.vcd_password,
                    self.ignore_ssl,
                    self.vcd_client
                )
        else:
            self.configure("default")
            collector = VcdCollector(
                self.vcd_host,
                self.vcd_user,
                self.vcd_org,
                self.vcd_password,
                self.ignore_ssl,
                self.vcd_client
            )

        metrics = collector.collect()

        def onError(err):
            log("Collection Error: {}".format(err))

        metrics.addErrback(onError)

        def onSuccess(metric_list):
            registry = CollectorRegistry()
            registry.register(ListCollector(metric_list))
            output = generate_latest(registry)

            request.setHeader("Content-Type", "text/plain; charset=UTF-8")
            request.setResponseCode(200)
            request.write(output)
            request.finish()

        metrics.addCallback(onSuccess)

        return NOT_DONE_YET


class VcdCollector:
    """
    Class for vCD collector
    """

    def __init__(self, vcd_host, vcd_user, vcd_org, vcd_password, ignore_ssl, vcd_client):
        self.vcd_host = vcd_host
        self.vcd_user = vcd_user
        self.vcd_org = vcd_org
        self.vcd_password = vcd_password
        self.ignore_ssl = ignore_ssl
        self.vcd_client = vcd_client
        self.vdc_resources = None

    def collect(self):
        metric_list = dict()
        metric_list['org'] = {
            'vcd_org_is_enabled': GaugeMetricFamily(
                'vcd_org_is_enabled',
                json.dumps({
                    "Description": "Enabled status of Organization",
                    "Enabled": 1,
                    "Disabled": 0
                }),
                labels=['org_name', 'org_full_name', 'org_id'])
        }
        metric_list['vdc'] = {
            'vcd_vdc_cpu_allocated': GaugeMetricFamily(
                'vcd_vdc_cpu_allocated',
                'CPU allocated to vdc',
                labels=['vdc_id', 'vdc_name', 'org_id', 'org_name', 'vdc_is_enabled', 'allocation_model']),
            'vcd_vdc_mhz_to_vcpu': GaugeMetricFamily(
                'vcd_vdc_mhz_to_vcpu',
                'Mhz to vCPU ratio of vdc',
                labels=['vdc_id', 'vdc_name', 'org_id', 'org_name', 'vdc_is_enabled', 'allocation_model']),
            'vcd_vdc_memory_allocated': GaugeMetricFamily(
                'vcd_vdc_memory_allocated',
                'Memory allocated to vdc',
                labels=['vdc_id', 'vdc_name', 'org_id', 'org_name', 'vdc_is_enabled', 'allocation_model']),
            'vcd_vdc_memory_used_bytes': GaugeMetricFamily(
                'vcd_vdc_memory_used_bytes',
                'Memory used by vdc in bytes',
                labels=['vdc_id', 'vdc_name', 'org_id', 'org_name', 'vdc_is_enabled', 'allocation_model']),
            'vcd_vdc_used_network_count': GaugeMetricFamily(
                'vcd_vdc_used_network_count',
                'Number of networks used by vdc',
                labels=['vdc_id', 'vdc_name', 'org_id', 'org_name', 'vdc_is_enabled', 'allocation_model']),
        }

        """
        {
            "Description": "Status of vApp",
            "Status": {
                "FAILED_CREATION": -1,
                "UNRESOLVED": 0,
                "RESOLVED": 1,
                "DEPLOYED": 2,
                "SUSPENDED": 3,
                "POWERED_ON": 4,
                "WAITING_FOR_INPUT": 5,
                "UNKNOWN": 6,
                "UNRECOGNIZED": 7,
                "POWERED_OFF": 8,
                "INCONSISTENT_STATE": 9,
                "MIXED": 10
            }
        }
        """

        metric_list['vapp_resources'] = {
            'vcd_vdc_vapp_status': GaugeMetricFamily(
                'vcd_vdc_vapp_status',
                'Status of vApp',
                labels=['vapp_id', 'vapp_name', 'vapp_deployed', 'vapp_status', 'vdc_id', 'vdc_name',
                        'org_id', 'org_name', 'vdc_is_enabled']
            ),
            'vcd_vdc_vapp_in_maintenance': GaugeMetricFamily(
                'vcd_vdc_vapp_in_maintenance',
                'Status of maintenance mode of given vApp',
                labels=['vapp_id', 'vapp_name', 'vapp_deployed', 'vdc_id', 'vdc_name',
                        'org_id', 'org_name', 'vdc_is_enabled'])
        }

        """
        {
            "Description": 'Status of VM',
            "States": {
                "FAILED_CREATION": -1,
                "UNRESOLVED": 0,
                "RESOLVED": 1,
                "DEPLOYED": 2,
                "SUSPENDED": 3,
                "POWERED_ON": 4,
                "WAITING_FOR_INPUT": 5,
                "UNKNOWN": 6,
                "UNRECOGNIZED": 7,
                "POWERED_OFF": 8,
                "INCONSISTENT_STATE": 9,
                "MIXED": 10
            }
        }
        """

        metric_list['vm_resources'] = {
            'vcd_vdc_vapp_vm_status': GaugeMetricFamily(
                'vcd_vdc_vapp_vm_status',
                'Status of VM',
                labels=['vm_id', 'vm_name', 'vm_deployed', 'vm_status', 'vm_os_type', 'vapp_id',
                        'vapp_name', 'vapp_deployed', 'vdc_id', 'vdc_name', 'org_id', 'org_name',
                        'vdc_is_enabled']
            ),
            'vcd_vdc_vapp_vm_vcpu': GaugeMetricFamily(
                'vcd_vdc_vapp_vm_vcpu',
                'vCPU count of vm in given vApp of vdc',
                labels=['vm_id', 'vm_name', 'vm_deployed', 'vm_status', 'vm_os_type', 'vapp_id',
                        'vapp_name', 'vapp_deployed', 'vdc_id', 'vdc_name', 'org_id', 'org_name',
                        'vdc_is_enabled']
            ),
            'vcd_vdc_vapp_vm_allocated_memory_mb': GaugeMetricFamily(
                'vcd_vdc_vapp_vm_allocated_memory_mb',
                'Memory allocated to VM of given vApp of vdc',
                labels=['vm_id', 'vm_name', 'vm_deployed', 'vm_status', 'vm_os_type', 'vapp_id',
                        'vapp_name', 'vapp_deployed', 'vdc_id', 'vdc_name', 'org_id', 'org_name',
                        'vdc_is_enabled'])
        }

        metrics = {}
        for key in metric_list.keys():
            metrics.update(metric_list[key])

        self._vcd_orgs_collect(metrics)

        return defer.succeed(list(metrics.values()))

    def _vcd_orgs_collect(self, metrics):
        start = datetime.utcnow()
        try:
            orgs = self.vcd_client.get_org_list()
            for org in orgs:
                org = Org(self.vcd_client, resource=org)
                org_labels = [str(org.resource.attrib['id']), str(org.get_name())]

                self.vdcs = self._vcd_vdc_resources_collect(org)

                def onError(err):
                    log("Unable to gather vDC: {}".format(err))

                self.vdcs.addErrback(onError)

                def onSuccess(resources):
                    self.vdcs = resources

                self.vdcs.addCallback(onSuccess)

                if not self.vdcs:
                    log("Org has no vDC: {}".format(str(org.get_name())))
                    continue
                else:
                    metrics['vcd_org_is_enabled'].add_metric(org_labels, org.update_org()['IsEnabled'])

                try:
                    for vdc_resource in self.vdcs:
                        vdc = VDC(self.vcd_client, resource=org.get_vdc(vdc_resource['name']))
                        vdc_labels = [vdc.resource.attrib['id'],
                                      vdc.name,
                                      org.resource.attrib['id'],
                                      str(org.get_name()),
                                      str(vdc.resource.IsEnabled),
                                      str(vdc.resource.AllocationModel.text)]
                        compute_capacity = vdc.resource.ComputeCapacity

                        metrics['vcd_vdc_cpu_allocated'].add_metric(vdc_labels, compute_capacity.Cpu.Allocated)
                        metrics['vcd_vdc_mhz_to_vcpu'].add_metric(vdc_labels, vdc.resource.VCpuInMhz2)
                        metrics['vcd_vdc_memory_allocated'].add_metric(vdc_labels, compute_capacity.Memory.Allocated)
                        metrics['vcd_vdc_memory_used_bytes'].add_metric(
                            vdc_labels,
                            compute_capacity.Memory.Used)  # Need to normalize
                        metrics['vcd_vdc_used_network_count'].add_metric(vdc_labels, vdc.resource.UsedNetworkCount)

                        self.vapps = self._vcd_vdc_vapp_resources_collect(vdc)

                        def onError(err):
                            log("Unable to Gather vApp Resources: {}".format(err))

                        self.vapps.addErrback(onError)

                        def onSuccess(resources):
                            self.vapps = resources

                        self.vapps.addCallback(onSuccess)

                        try:
                            for vapp_resource in self.vapps:
                                vapp = VApp(self.vcd_client, resource=vdc.get_vapp(vapp_resource['name']))
                                vapp_labels = [
                                    vapp.resource.attrib['id'],
                                    vapp.resource.attrib['name'],
                                    vapp.resource.attrib['deployed'],
                                    vapp.resource.attrib['status'],
                                    vdc.resource.attrib['id'],
                                    vdc.name,
                                    org.resource.attrib['id'],
                                    str(org.get_name()),
                                    str(vdc.resource.IsEnabled)
                                ]
                                metrics['vcd_vdc_vapp_status'].add_metric(vapp_labels, vapp.resource.attrib['status'])
                                metrics['vcd_vdc_vapp_in_maintenance'].add_metric(vapp_labels,
                                                                                  vapp.resource.InMaintenanceMode)

                                self.vms = self._vcd_vdc_vapp_vm_resources_collect(vapp)

                                def onError(err):
                                    log("Unable to Gather vApp Resources: {}".format(err))

                                self.vms.addErrback(onError)

                                def onSuccess(resources):
                                    self.vms = resources

                                self.vms.addCallback(onSuccess)

                                try:
                                    for vm in self.vms:
                                        vm_labels = [
                                            vm.attrib['id'],
                                            vm.attrib['name'],
                                            vm.attrib['deployed'],
                                            vm.attrib['status'],
                                            vapp.resource.attrib['id'],
                                            vapp.resource.attrib['name'],
                                            vapp.resource.attrib['deployed'],
                                            vdc.resource.attrib['id'],
                                            vdc.name,
                                            org.resource.attrib['id'],
                                            str(org.get_name()),
                                            str(vdc.resource.IsEnabled)
                                        ]
                                        vm_spec = vm.VmSpecSection
                                        metrics['vcd_vdc_vapp_vm_status'].add_metric(vm_labels,
                                                                                     vm.attrib['status'])

                                        metrics['vcd_vdc_vapp_vm_vcpu'].add_metric(vm_labels, vm_spec.NumCpus)
                                        metrics['vcd_vdc_vapp_vm_allocated_memory_mb'].add_metric(
                                            vm_labels,
                                            vm_spec.MemoryResourceMb.Configured)
                                except Exception as err:
                                    log("Unable to poll VM: {}".format(err))
                                    pass
                        except Exception as err:
                            log("Unable to poll vApp: {}".format(err))
                            pass
                except Exception as err:
                    log("Unable to poll vDC: {}".format(err))
                    pass
        except Exception as err:
            log("Unable to poll vOrg: {}".format(err))
            pass

        log("Finished All vOrg Metrics Collection: ({})".format(datetime.utcnow() - start))
        self.vcd_client.logout()

        return list(metrics.values())

    @staticmethod
    def _vcd_vdc_resources_collect(org):
        vdc_resources = org.list_vdcs()
        return defer.succeed(vdc_resources)

    @staticmethod
    def _vcd_vdc_vapp_resources_collect(vdc):
        vapp_resources = vdc.list_resources(EntityType.VAPP)
        return defer.succeed(vapp_resources)

    @staticmethod
    def _vcd_vdc_vapp_vm_resources_collect(vapp):
        vm_resources = vapp.get_all_vms()
        return defer.succeed(vm_resources)


class HealthzResource(Resource):
    """
    Class for exporter healthz
    """

    def render_GET(self, request):
        request.setHeader("Content-Type", "text/plain; charset=UTF-8")
        request.setResponseCode(200)
        log("Healthz")
        return 'Server is UP'.encode()


class MetricsResource(Resource):
    """
    Class for exporter metrics
    """

    def render_GET(self, request):
        request.setHeader("Content-Type", "text/plain; charset=UTF-8")
        request.setResponseCode(200)
        log("Metrics")
        return generate_latest()


class VcdConnection:
    """
    Class for vCD connection context
    """

    def __str__(self):
        return str(self.vcd_client)

    def __repr__(self):
        return "<vCD Connection: {}>".format(self.vcd_client)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_tb is None:
            log("Connection Created: {}".format(self))
        else:
            log("Connection ({}) ERROR: Type: {}, Value: {}, Traceback: {}".format(self, exc_type, exc_val, exc_tb))

    def __init__(self, vcd_user, vcd_org, vcd_password, vcd_host, ignore_ssl):
        # Create vCD Client Connection
        try:
            self.vcd_client = Client(
                vcd_host,
                api_version='31.0',
                verify_ssl_certs=ignore_ssl
            )
            self.vcd_client.set_credentials(BasicLoginCredentials(vcd_user, vcd_org, vcd_password))
        except Exception as err:
            self.__exit__(
                "ConnectionFailed",
                "126",
                err
            )

    def connection(self):
        return defer.succeed(self.vcd_client)


def main(argv=None):
    """
    Main entry point.
    """

    parser = ArgumentParser(description='vCD metrics exporter for Prometheus')
    parser.add_argument('-c', '--config', dest='config_file',
                        default=None, help="configuration file")
    parser.add_argument('-p', '--port', dest='port', type=int,
                        default=9274, help="HTTP port to expose metrics")

    args = parser.parse_args(argv or sys.argv[1:])

    # Flag for improvements
    # reactor.suggestThreadPoolSize(25)

    root = Resource()
    root.putChild(b'healthz', HealthzResource())
    root.putChild(b'metrics', MetricsResource())
    root.putChild(b'vcd', VcdApplicationResource(args))

    factory = Site(root)
    endpoint = endpoints.TCP4ServerEndpoint(reactor, args.port)
    endpoint.listen(factory)
    reactor.run()


if __name__ == '__main__':
    main()
