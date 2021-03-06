# -*- coding: utf-8 -*-

from cached_property import cached_property

import docker
import docker.errors
import docker.utils


class DockerRunner(object):
    def __init__(self, ctx, default_platform=None):
        self.ctx = ctx
        self.default_platform = default_platform

        self.running_containers = []

    @cached_property
    def client(self):
        return docker.Client(
            version='auto',
            **docker.utils.kwargs_from_env(assert_hostname=False))

    def setup(self):
        self.pull_required_images()

        inventory_lines = []

        for host, container in self.start_containers():
            additional_config = self.additional_config_for_host(host, container)
            inventory_hostname = host.vars()['inventory_hostname']
            inventory_host_options = \
                ' '.join('{0}="{1}"'.format(key, value)
                         for key, value in additional_config.items())
            inventory_line = ' '.join((inventory_hostname, inventory_host_options))
            inventory_lines.append(inventory_line)

        inventory_content = '\n'.join(inventory_lines)

        self.ctx.extended_inventory_path.join('goodplay').write(inventory_content)

    def pull_required_images(self):
        required_images = set()

        for host in self.ctx.inventory.hosts():
            required_image = self.get_docker_image_for_host(host)

            if required_image:
                required_images.add(required_image)

        # TODO: define how to disable this caching and always pull latest
        for required_image in required_images:
            try:
                self.client.inspect_image(required_image)
            except docker.errors.NotFound:
                self.client.pull(required_image)

    def get_docker_image_for_host(self, host):
        host_vars = host.vars()
        goodplay_platform = host_vars.get('goodplay_platform')

        if goodplay_platform == '*':
            return self.default_platform.image
        elif goodplay_platform:
            platform = self.ctx.platform_manager.find_by_id(goodplay_platform)

            if not platform:
                raise Exception(
                    "goodplay_platform '{0}' specified in inventory for "
                    "host '{1}' not found in .goodplay.yml".format(
                        goodplay_platform, host_vars['inventory_hostname']))

            return platform.image

        return host_vars.get('goodplay_image')

    def start_containers(self):
        for host in self.ctx.inventory.hosts():
            if not self.get_docker_image_for_host(host):
                continue

            container = self.start_container(host)
            self.running_containers.append(container)

            yield host, container

    def start_container(self, host):
        hostname = host.vars()['inventory_hostname']
        image = self.get_docker_image_for_host(host)

        # host_config:
        # probably create a new network stack for the first container
        #   (network_mode='bridge')
        # and then reuse this network stack for the other containers
        #   (network_mode='container:[name|id]')
        #
        # cap_add probably needed when supporting KVM

        container = self.client.create_container(
            image=image,
            hostname=hostname,
            detach=True,
            tty=True,
            host_config=self.client.create_host_config()
        )

        self.client.start(container)

        return container

    def additional_config_for_host(self, host, container):
        return dict(
            ansible_connection='docker',
            ansible_host=container['Id'],
        )

    def teardown(self):
        # kill and remove containers
        for container in self.running_containers:
            self.client.remove_container(container, force=True)
