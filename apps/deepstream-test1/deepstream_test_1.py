#!/usr/bin/env python3

################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2019-2022 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
################################################################################

import sys
sys.path.append('../')
import os
import gi
gi.require_version('Gst', '1.0')
from gi.repository import GLib, Gst
from common.is_aarch_64 import is_aarch64
from common.bus_call import bus_call

import pyds

PGIE_CLASS_ID_VEHICLE = 0
PGIE_CLASS_ID_BICYCLE = 1
PGIE_CLASS_ID_PERSON = 2
PGIE_CLASS_ID_ROADSIGN = 3


def osd_sink_pad_buffer_probe(pad,info,u_data):
    frame_number=0
    num_rects=0

    gst_buffer = info.get_buffer()
    if not gst_buffer:
        print("Unable to get GstBuffer ")
        return

    batch_meta = pyds.gst_buffer_get_nvds_batch_meta(hash(gst_buffer))
    l_frame = batch_meta.frame_meta_list
    while l_frame is not None:
        try:
            frame_meta = pyds.NvDsFrameMeta.cast(l_frame.data)
        except StopIteration:
            break

        obj_counter = {
            PGIE_CLASS_ID_VEHICLE:0,
            PGIE_CLASS_ID_PERSON:0,
            PGIE_CLASS_ID_BICYCLE:0,
            PGIE_CLASS_ID_ROADSIGN:0
        }
        frame_number=frame_meta.frame_num
        num_rects = frame_meta.num_obj_meta
        l_obj=frame_meta.obj_meta_list
        while l_obj is not None:
            try:
                obj_meta=pyds.NvDsObjectMeta.cast(l_obj.data)
            except StopIteration:
                break
            obj_counter[obj_meta.class_id] += 1
            obj_meta.rect_params.border_color.set(0.0, 0.0, 1.0, 0.8)
            # 打印识别框坐标
            left = obj_meta.rect_params.left
            top = obj_meta.rect_params.top
            width = obj_meta.rect_params.width
            height = obj_meta.rect_params.height
            print(f"Object {obj_meta.class_id} bbox: left={left}, top={top}, width={width}, height={height}")
            try: 
                l_obj=l_obj.next
            except StopIteration:
                break

        display_meta=pyds.nvds_acquire_display_meta_from_pool(batch_meta)
        display_meta.num_labels = 1
        py_nvosd_text_params = display_meta.text_params[0]
        py_nvosd_text_params.display_text = "Frame Number={} Number of Objects={} Vehicle_count={} Person_count={}".format(frame_number, num_rects, obj_counter[PGIE_CLASS_ID_VEHICLE], obj_counter[PGIE_CLASS_ID_PERSON])
        py_nvosd_text_params.x_offset = 10
        py_nvosd_text_params.y_offset = 12
        py_nvosd_text_params.font_params.font_name = "Serif"
        py_nvosd_text_params.font_params.font_size = 10
        py_nvosd_text_params.font_params.font_color.set(1.0, 1.0, 1.0, 1.0)
        py_nvosd_text_params.set_bg_clr = 1
        py_nvosd_text_params.text_bg_clr.set(0.0, 0.0, 0.0, 1.0)
        print(pyds.get_string(py_nvosd_text_params.display_text))
        pyds.nvds_add_display_meta_to_frame(frame_meta, display_meta)
        try:
            l_frame=l_frame.next
        except StopIteration:
            break
    return Gst.PadProbeReturn.OK


def main(args):
    # 不再检查输入参数，摄像头模式无需参数
    Gst.init(None)
    print("Creating Pipeline\n ")
    pipeline = Gst.Pipeline()
    if not pipeline:
        sys.stderr.write(" Unable to create Pipeline \n")

    # 摄像头输入
    source = Gst.ElementFactory.make("v4l2src", "camera-source")
    if not source:
        sys.stderr.write(" Unable to create Camera Source \n")
    source.set_property('device', '/dev/video0')

    videoconvert = Gst.ElementFactory.make("videoconvert", "video-convert")
    if not videoconvert:
        sys.stderr.write(" Unable to create videoconvert \n")

    caps_nv12 = Gst.ElementFactory.make("capsfilter", "nv12-caps")
    caps_nv12.set_property("caps", Gst.Caps.from_string("video/x-raw,format=NV12,width=640,height=480,framerate=30/1"))

    nvvidconv = Gst.ElementFactory.make("nvvideoconvert", "nvvideo-converter")
    if not nvvidconv:
        sys.stderr.write(" Unable to create nvvideoconvert \n")

    caps_nvmm = Gst.ElementFactory.make("capsfilter", "nvmm-caps")
    caps_nvmm.set_property("caps", Gst.Caps.from_string("video/x-raw(memory:NVMM),format=NV12,width=640,height=480,framerate=30/1"))

    streammux = Gst.ElementFactory.make("nvstreammux", "Stream-muxer")
    if not streammux:
        sys.stderr.write(" Unable to create NvStreamMux \n")
    streammux.set_property('width', 640)
    streammux.set_property('height', 480)
    streammux.set_property('batch-size', 1)
    streammux.set_property('batched-push-timeout', 4000000)

    pgie = Gst.ElementFactory.make("nvinfer", "primary-inference")
    if not pgie:
        sys.stderr.write(" Unable to create pgie \n")
    pgie.set_property('config-file-path', "dstest1_pgie_config.txt")

    nvvidconv2 = Gst.ElementFactory.make("nvvideoconvert", "convertor")
    if not nvvidconv2:
        sys.stderr.write(" Unable to create nvvidconv2 \n")

    nvosd = Gst.ElementFactory.make("nvdsosd", "onscreendisplay")
    if not nvosd:
        sys.stderr.write(" Unable to create nvosd \n")

    print("Creating EGLSink \n")
    sink = Gst.ElementFactory.make("nveglglessink", "nvvideo-renderer")
    if not sink:
        sys.stderr.write(" Unable to create egl sink \n")

    print("Adding elements to Pipeline \n")
    pipeline.add(source)
    pipeline.add(videoconvert)
    pipeline.add(caps_nv12)
    pipeline.add(nvvidconv)
    pipeline.add(caps_nvmm)
    pipeline.add(streammux)
    pipeline.add(pgie)
    pipeline.add(nvvidconv2)
    pipeline.add(nvosd)
    pipeline.add(sink)

    print("Linking elements in the Pipeline \n")
    source.link(videoconvert)
    videoconvert.link(caps_nv12)
    caps_nv12.link(nvvidconv)
    nvvidconv.link(caps_nvmm)
    # caps_nvmm -> streammux 用 request pad
    sinkpad = streammux.get_request_pad("sink_0")
    if not sinkpad:
        sys.stderr.write(" Unable to get the sink pad of streammux \n")
    srcpad = caps_nvmm.get_static_pad("src")
    if not srcpad:
        sys.stderr.write(" Unable to get source pad of caps_nvmm \n")
    srcpad.link(sinkpad)
    # 后续直接 link
    streammux.link(pgie)
    pgie.link(nvvidconv2)
    nvvidconv2.link(nvosd)
    nvosd.link(sink)

    loop = GLib.MainLoop()
    bus = pipeline.get_bus()
    bus.add_signal_watch()
    bus.connect ("message", bus_call, loop)

    osdsinkpad = nvosd.get_static_pad("sink")
    if not osdsinkpad:
        sys.stderr.write(" Unable to get sink pad of nvosd \n")
    osdsinkpad.add_probe(Gst.PadProbeType.BUFFER, osd_sink_pad_buffer_probe, 0)

    print("Starting pipeline \n")
    pipeline.set_state(Gst.State.PLAYING)
    try:
        loop.run()
    except:
        pass
    pipeline.set_state(Gst.State.NULL)

if __name__ == '__main__':
    sys.exit(main(sys.argv))

