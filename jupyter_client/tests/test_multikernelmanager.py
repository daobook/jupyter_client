"""Tests for the notebook kernel and session manager."""
import asyncio
import concurrent.futures
import os
import sys
import uuid
from subprocess import PIPE
from unittest import TestCase

import pytest
from jupyter_core import paths
from tornado.testing import AsyncTestCase
from tornado.testing import gen_test
from traitlets.config.loader import Config

from ..localinterfaces import localhost
from .utils import AsyncKMSubclass
from .utils import AsyncMKMSubclass
from .utils import install_kernel
from .utils import skip_win32
from .utils import SyncKMSubclass
from .utils import SyncMKMSubclass
from .utils import test_env
from jupyter_client import AsyncKernelManager
from jupyter_client import KernelManager
from jupyter_client.multikernelmanager import AsyncMultiKernelManager
from jupyter_client.multikernelmanager import MultiKernelManager

TIMEOUT = 30


class TestKernelManager(TestCase):
    def setUp(self):
        self.env_patch = test_env()
        self.env_patch.start()
        super().setUp()

    # static so picklable for multiprocessing on Windows
    @staticmethod
    def _get_tcp_km():
        c = Config()
        return MultiKernelManager(config=c)

    @staticmethod
    def _get_tcp_km_sub():
        c = Config()
        return SyncMKMSubclass(config=c)

    # static so picklable for multiprocessing on Windows
    @staticmethod
    def _get_ipc_km():
        c = Config()
        c.KernelManager.transport = "ipc"
        c.KernelManager.ip = "test"
        return MultiKernelManager(config=c)

    # static so picklable for multiprocessing on Windows
    @staticmethod
    def _run_lifecycle(km, test_kid=None):
        if test_kid:
            kid = km.start_kernel(stdout=PIPE, stderr=PIPE, kernel_id=test_kid)
            assert kid == test_kid
        else:
            kid = km.start_kernel(stdout=PIPE, stderr=PIPE)
        assert km.is_alive(kid)
        assert km.get_kernel(kid).ready.done()
        assert kid in km
        assert kid in km.list_kernel_ids()
        assert len(km) == 1, f"{len(km)} != {1}"
        km.restart_kernel(kid, now=True)
        assert km.is_alive(kid)
        assert kid in km.list_kernel_ids()
        km.interrupt_kernel(kid)
        k = km.get_kernel(kid)
        assert isinstance(k, KernelManager)
        km.shutdown_kernel(kid, now=True)
        assert kid not in km, f"{kid} not in {km}"

    def _run_cinfo(self, km, transport, ip):
        kid = km.start_kernel(stdout=PIPE, stderr=PIPE)
        km.get_kernel(kid)
        cinfo = km.get_connection_info(kid)
        self.assertEqual(transport, cinfo["transport"])
        self.assertEqual(ip, cinfo["ip"])
        self.assertTrue("stdin_port" in cinfo)
        self.assertTrue("iopub_port" in cinfo)
        stream = km.connect_iopub(kid)
        stream.close()
        self.assertTrue("shell_port" in cinfo)
        stream = km.connect_shell(kid)
        stream.close()
        self.assertTrue("hb_port" in cinfo)
        stream = km.connect_hb(kid)
        stream.close()
        km.shutdown_kernel(kid, now=True)

    # static so picklable for multiprocessing on Windows
    @classmethod
    def test_tcp_lifecycle(cls):
        km = cls._get_tcp_km()
        cls._run_lifecycle(km)

    def test_tcp_lifecycle_with_kernel_id(self):
        km = self._get_tcp_km()
        self._run_lifecycle(km, test_kid=str(uuid.uuid4()))

    def test_shutdown_all(self):
        km = self._get_tcp_km()
        kid = km.start_kernel(stdout=PIPE, stderr=PIPE)
        self.assertIn(kid, km)
        km.shutdown_all()
        self.assertNotIn(kid, km)
        # shutdown again is okay, because we have no kernels
        km.shutdown_all()

    def test_tcp_cinfo(self):
        km = self._get_tcp_km()
        self._run_cinfo(km, "tcp", localhost())

    @skip_win32
    def test_ipc_lifecycle(self):
        km = self._get_ipc_km()
        self._run_lifecycle(km)

    @skip_win32
    def test_ipc_cinfo(self):
        km = self._get_ipc_km()
        self._run_cinfo(km, "ipc", "test")

    def test_start_sequence_tcp_kernels(self):
        """Ensure that a sequence of kernel startups doesn't break anything."""
        self._run_lifecycle(self._get_tcp_km())
        self._run_lifecycle(self._get_tcp_km())
        self._run_lifecycle(self._get_tcp_km())

    @skip_win32
    def test_start_sequence_ipc_kernels(self):
        """Ensure that a sequence of kernel startups doesn't break anything."""
        self._run_lifecycle(self._get_ipc_km())
        self._run_lifecycle(self._get_ipc_km())
        self._run_lifecycle(self._get_ipc_km())

    def tcp_lifecycle_with_loop(self):
        # Ensure each thread has an event loop
        asyncio.set_event_loop(asyncio.new_event_loop())
        self.test_tcp_lifecycle()

    def test_start_parallel_thread_kernels(self):
        self.test_tcp_lifecycle()

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as thread_executor:
            future1 = thread_executor.submit(self.tcp_lifecycle_with_loop)
            future2 = thread_executor.submit(self.tcp_lifecycle_with_loop)
            future1.result()
            future2.result()

    @pytest.mark.skipif(
        (sys.platform == "darwin") and (sys.version_info >= (3, 6)) and (sys.version_info < (3, 8)),
        reason='"Bad file descriptor" error',
    )
    def test_start_parallel_process_kernels(self):
        self.test_tcp_lifecycle()

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as thread_executor:
            future1 = thread_executor.submit(self.tcp_lifecycle_with_loop)
            with concurrent.futures.ProcessPoolExecutor(max_workers=1) as process_executor:
                # Windows tests needs this target to be picklable:
                future2 = process_executor.submit(self.test_tcp_lifecycle)
                future2.result()
            future1.result()

    def test_subclass_callables(self):
        km = self._get_tcp_km_sub()

        km.reset_counts()
        kid = km.start_kernel(stdout=PIPE, stderr=PIPE)
        assert km.call_count("start_kernel") == 1
        assert isinstance(km.get_kernel(kid), SyncKMSubclass)
        assert km.get_kernel(kid).call_count("start_kernel") == 1
        assert km.get_kernel(kid).call_count("_launch_kernel") == 1

        assert km.is_alive(kid)
        assert kid in km
        assert kid in km.list_kernel_ids()
        assert len(km) == 1, f"{len(km)} != {1}"

        km.get_kernel(kid).reset_counts()
        km.reset_counts()
        km.restart_kernel(kid, now=True)
        assert km.call_count("restart_kernel") == 1
        assert km.call_count("get_kernel") == 1
        assert km.get_kernel(kid).call_count("restart_kernel") == 1
        assert km.get_kernel(kid).call_count("shutdown_kernel") == 1
        assert km.get_kernel(kid).call_count("interrupt_kernel") == 1
        assert km.get_kernel(kid).call_count("_kill_kernel") == 1
        assert km.get_kernel(kid).call_count("cleanup_resources") == 1
        assert km.get_kernel(kid).call_count("start_kernel") == 1
        assert km.get_kernel(kid).call_count("_launch_kernel") == 1

        assert km.is_alive(kid)
        assert kid in km.list_kernel_ids()

        km.get_kernel(kid).reset_counts()
        km.reset_counts()
        km.interrupt_kernel(kid)
        assert km.call_count("interrupt_kernel") == 1
        assert km.call_count("get_kernel") == 1
        assert km.get_kernel(kid).call_count("interrupt_kernel") == 1

        km.get_kernel(kid).reset_counts()
        km.reset_counts()
        k = km.get_kernel(kid)
        assert isinstance(k, SyncKMSubclass)
        assert km.call_count("get_kernel") == 1

        km.get_kernel(kid).reset_counts()
        km.reset_counts()
        km.shutdown_all(now=True)
        assert km.call_count("shutdown_kernel") == 1
        assert km.call_count("remove_kernel") == 1
        assert km.call_count("request_shutdown") == 0
        assert km.call_count("finish_shutdown") == 0
        assert km.call_count("cleanup_resources") == 0

        assert kid not in km, f"{kid} not in {km}"


class TestAsyncKernelManager(AsyncTestCase):
    def setUp(self):
        self.env_patch = test_env()
        self.env_patch.start()
        super().setUp()

    # static so picklable for multiprocessing on Windows
    @staticmethod
    def _get_tcp_km():
        c = Config()
        return AsyncMultiKernelManager(config=c)

    @staticmethod
    def _get_tcp_km_sub():
        c = Config()
        return AsyncMKMSubclass(config=c)

    # static so picklable for multiprocessing on Windows
    @staticmethod
    def _get_ipc_km():
        c = Config()
        c.KernelManager.transport = "ipc"
        c.KernelManager.ip = "test"
        return AsyncMultiKernelManager(config=c)

    @staticmethod
    def _get_pending_kernels_km():
        c = Config()
        c.AsyncMultiKernelManager.use_pending_kernels = True
        return AsyncMultiKernelManager(config=c)

    # static so picklable for multiprocessing on Windows
    @staticmethod
    async def _run_lifecycle(km, test_kid=None):
        if test_kid:
            kid = await km.start_kernel(stdout=PIPE, stderr=PIPE, kernel_id=test_kid)
            assert kid == test_kid
        else:
            kid = await km.start_kernel(stdout=PIPE, stderr=PIPE)
        assert await km.is_alive(kid)
        assert kid in km
        assert kid in km.list_kernel_ids()
        assert len(km) == 1, f"{len(km)} != {1}"
        await km.restart_kernel(kid, now=True)
        assert await km.is_alive(kid)
        assert kid in km.list_kernel_ids()
        await km.interrupt_kernel(kid)
        k = km.get_kernel(kid)
        assert isinstance(k, AsyncKernelManager)
        await km.shutdown_kernel(kid, now=True)
        assert kid not in km, f"{kid} not in {km}"

    async def _run_cinfo(self, km, transport, ip):
        kid = await km.start_kernel(stdout=PIPE, stderr=PIPE)
        km.get_kernel(kid)
        cinfo = km.get_connection_info(kid)
        self.assertEqual(transport, cinfo["transport"])
        self.assertEqual(ip, cinfo["ip"])
        self.assertTrue("stdin_port" in cinfo)
        self.assertTrue("iopub_port" in cinfo)
        stream = km.connect_iopub(kid)
        stream.close()
        self.assertTrue("shell_port" in cinfo)
        stream = km.connect_shell(kid)
        stream.close()
        self.assertTrue("hb_port" in cinfo)
        stream = km.connect_hb(kid)
        stream.close()
        await km.shutdown_kernel(kid, now=True)
        self.assertNotIn(kid, km)

    @gen_test
    async def test_tcp_lifecycle(self):
        await self.raw_tcp_lifecycle()

    @gen_test
    async def test_tcp_lifecycle_with_kernel_id(self):
        await self.raw_tcp_lifecycle(test_kid=str(uuid.uuid4()))

    @gen_test
    async def test_shutdown_all(self):
        km = self._get_tcp_km()
        kid = await km.start_kernel(stdout=PIPE, stderr=PIPE)
        self.assertIn(kid, km)
        await km.shutdown_all()
        self.assertNotIn(kid, km)
        # shutdown again is okay, because we have no kernels
        await km.shutdown_all()

    @gen_test(timeout=20)
    async def test_use_after_shutdown_all(self):
        km = self._get_tcp_km()
        kid = await km.start_kernel(stdout=PIPE, stderr=PIPE)
        self.assertIn(kid, km)
        await km.shutdown_all()
        self.assertNotIn(kid, km)

        # Start another kernel
        kid = await km.start_kernel(stdout=PIPE, stderr=PIPE)
        self.assertIn(kid, km)
        await km.shutdown_all()
        self.assertNotIn(kid, km)
        # shutdown again is okay, because we have no kernels
        await km.shutdown_all()

    @gen_test(timeout=20)
    async def test_shutdown_all_while_starting(self):
        km = self._get_tcp_km()
        kid_future = asyncio.ensure_future(km.start_kernel(stdout=PIPE, stderr=PIPE))
        # This is relying on the ordering of the asyncio queue, not sure if guaranteed or not:
        kid, _ = await asyncio.gather(kid_future, km.shutdown_all())
        self.assertNotIn(kid, km)

        # Start another kernel
        kid = await km.start_kernel(stdout=PIPE, stderr=PIPE)
        self.assertIn(kid, km)
        self.assertEqual(len(km), 1)
        await km.shutdown_all()
        self.assertNotIn(kid, km)
        # shutdown again is okay, because we have no kernels
        await km.shutdown_all()

    @gen_test
    async def test_use_pending_kernels(self):
        km = self._get_pending_kernels_km()
        kid = await km.start_kernel(stdout=PIPE, stderr=PIPE)
        kernel = km.get_kernel(kid)
        assert not kernel.ready.done()
        assert kid in km
        assert kid in km.list_kernel_ids()
        assert len(km) == 1, f"{len(km)} != {1}"
        await kernel.ready
        await km.restart_kernel(kid, now=True)
        assert await km.is_alive(kid)
        assert kid in km.list_kernel_ids()
        await km.interrupt_kernel(kid)
        k = km.get_kernel(kid)
        assert isinstance(k, AsyncKernelManager)
        await km.shutdown_kernel(kid, now=True)
        assert kid not in km, f"{kid} not in {km}"

    @gen_test
    async def test_use_pending_kernels_early_restart(self):
        km = self._get_pending_kernels_km()
        kid = await km.start_kernel(stdout=PIPE, stderr=PIPE)
        kernel = km.get_kernel(kid)
        assert not kernel.ready.done()
        with pytest.raises(RuntimeError):
            await km.restart_kernel(kid, now=True)
        await kernel.ready
        await km.shutdown_kernel(kid, now=True)
        assert kid not in km, f"{kid} not in {km}"

    @gen_test
    async def test_use_pending_kernels_early_shutdown(self):
        km = self._get_pending_kernels_km()
        kid = await km.start_kernel(stdout=PIPE, stderr=PIPE)
        kernel = km.get_kernel(kid)
        assert not kernel.ready.done()
        await km.shutdown_kernel(kid, now=True)
        assert kid not in km, f"{kid} not in {km}"

    @gen_test
    async def test_use_pending_kernels_early_interrupt(self):
        km = self._get_pending_kernels_km()
        kid = await km.start_kernel(stdout=PIPE, stderr=PIPE)
        kernel = km.get_kernel(kid)
        assert not kernel.ready.done()
        with pytest.raises(RuntimeError):
            await km.interrupt_kernel(kid)
        await km.shutdown_kernel(kid, now=True)
        assert kid not in km, f"{kid} not in {km}"

    @gen_test
    async def test_tcp_cinfo(self):
        km = self._get_tcp_km()
        await self._run_cinfo(km, "tcp", localhost())

    @skip_win32
    @gen_test
    async def test_ipc_lifecycle(self):
        km = self._get_ipc_km()
        await self._run_lifecycle(km)

    @skip_win32
    @gen_test
    async def test_ipc_cinfo(self):
        km = self._get_ipc_km()
        await self._run_cinfo(km, "ipc", "test")

    @gen_test
    async def test_start_sequence_tcp_kernels(self):
        """Ensure that a sequence of kernel startups doesn't break anything."""
        await self._run_lifecycle(self._get_tcp_km())
        await self._run_lifecycle(self._get_tcp_km())
        await self._run_lifecycle(self._get_tcp_km())

    @skip_win32
    @gen_test
    async def test_start_sequence_ipc_kernels(self):
        """Ensure that a sequence of kernel startups doesn't break anything."""
        await self._run_lifecycle(self._get_ipc_km())
        await self._run_lifecycle(self._get_ipc_km())
        await self._run_lifecycle(self._get_ipc_km())

    def tcp_lifecycle_with_loop(self):
        # Ensure each thread has an event loop
        asyncio.set_event_loop(asyncio.new_event_loop())
        asyncio.get_event_loop().run_until_complete(self.raw_tcp_lifecycle())

    # static so picklable for multiprocessing on Windows
    @classmethod
    async def raw_tcp_lifecycle(cls, test_kid=None):
        # Since @gen_test creates an event loop, we need a raw form of
        # test_tcp_lifecycle that assumes the loop already exists.
        km = cls._get_tcp_km()
        await cls._run_lifecycle(km, test_kid=test_kid)

    # static so picklable for multiprocessing on Windows
    @classmethod
    def raw_tcp_lifecycle_sync(cls, test_kid=None):
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Forked MP, make new loop
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        loop.run_until_complete(cls.raw_tcp_lifecycle(test_kid=test_kid))

    @gen_test
    async def test_start_parallel_thread_kernels(self):
        await self.raw_tcp_lifecycle()

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as thread_executor:
            future1 = thread_executor.submit(self.tcp_lifecycle_with_loop)
            future2 = thread_executor.submit(self.tcp_lifecycle_with_loop)
            future1.result()
            future2.result()

    @gen_test
    async def test_start_parallel_process_kernels(self):
        await self.raw_tcp_lifecycle()

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as thread_executor:
            future1 = thread_executor.submit(self.tcp_lifecycle_with_loop)
            with concurrent.futures.ProcessPoolExecutor(max_workers=1) as process_executor:
                # Windows tests needs this target to be picklable:
                future2 = process_executor.submit(self.raw_tcp_lifecycle_sync)
                future2.result()
            future1.result()

    @gen_test
    async def test_subclass_callables(self):
        mkm = self._get_tcp_km_sub()

        mkm.reset_counts()
        kid = await mkm.start_kernel(stdout=PIPE, stderr=PIPE)
        assert mkm.call_count("start_kernel") == 1
        assert isinstance(mkm.get_kernel(kid), AsyncKMSubclass)
        assert mkm.get_kernel(kid).call_count("start_kernel") == 1
        assert mkm.get_kernel(kid).call_count("_launch_kernel") == 1

        assert await mkm.is_alive(kid)
        assert kid in mkm
        assert kid in mkm.list_kernel_ids()
        assert len(mkm) == 1, f"{len(mkm)} != {1}"

        mkm.get_kernel(kid).reset_counts()
        mkm.reset_counts()
        await mkm.restart_kernel(kid, now=True)
        assert mkm.call_count("restart_kernel") == 1
        assert mkm.call_count("get_kernel") == 1
        assert mkm.get_kernel(kid).call_count("restart_kernel") == 1
        assert mkm.get_kernel(kid).call_count("shutdown_kernel") == 1
        assert mkm.get_kernel(kid).call_count("interrupt_kernel") == 1
        assert mkm.get_kernel(kid).call_count("_kill_kernel") == 1
        assert mkm.get_kernel(kid).call_count("cleanup_resources") == 1
        assert mkm.get_kernel(kid).call_count("start_kernel") == 1
        assert mkm.get_kernel(kid).call_count("_launch_kernel") == 1

        assert await mkm.is_alive(kid)
        assert kid in mkm.list_kernel_ids()

        mkm.get_kernel(kid).reset_counts()
        mkm.reset_counts()
        await mkm.interrupt_kernel(kid)
        assert mkm.call_count("interrupt_kernel") == 1
        assert mkm.call_count("get_kernel") == 1
        assert mkm.get_kernel(kid).call_count("interrupt_kernel") == 1

        mkm.get_kernel(kid).reset_counts()
        mkm.reset_counts()
        k = mkm.get_kernel(kid)
        assert isinstance(k, AsyncKMSubclass)
        assert mkm.call_count("get_kernel") == 1

        mkm.get_kernel(kid).reset_counts()
        mkm.reset_counts()
        await mkm.shutdown_all(now=True)
        assert mkm.call_count("shutdown_kernel") == 1
        assert mkm.call_count("remove_kernel") == 1
        assert mkm.call_count("request_shutdown") == 0
        assert mkm.call_count("finish_shutdown") == 0
        assert mkm.call_count("cleanup_resources") == 0

        assert kid not in mkm, f"{kid} not in {mkm}"

    @gen_test
    async def test_bad_kernelspec(self):
        km = self._get_tcp_km()
        install_kernel(
            os.path.join(paths.jupyter_data_dir(), "kernels"),
            argv=["non_existent_executable"],
            name="bad",
        )
        with pytest.raises(FileNotFoundError):
            await km.start_kernel(kernel_name="bad", stdout=PIPE, stderr=PIPE)

    @gen_test
    async def test_bad_kernelspec_pending(self):
        km = self._get_pending_kernels_km()
        install_kernel(
            os.path.join(paths.jupyter_data_dir(), "kernels"),
            argv=["non_existent_executable"],
            name="bad",
        )
        kernel_id = await km.start_kernel(kernel_name="bad", stdout=PIPE, stderr=PIPE)
        assert kernel_id in km._starting_kernels
        with pytest.raises(FileNotFoundError):
            await km.get_kernel(kernel_id).ready
        assert kernel_id in km.list_kernel_ids()
        await km.shutdown_kernel(kernel_id)
        assert kernel_id not in km.list_kernel_ids()
