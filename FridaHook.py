import sys
import os
import frida
import hashlib
import json
import numpy
import OpenEXR
import Imath
from PIL import Image

# For frida version 15 or higher
# Please use real App name for `name` and package name for `identifier` on Android
# For Android native processes or iOS processes, keep identifier and name as same
processes_to_hook = [
    # Android
    # {'identifier': 'com.ss.android.ugc.aweme', 'name': '抖音'},
    # {'identifier': 'com.mt.mtxx.mtxx', 'name': '美图秀秀'},
    {'identifier': 'com.meitu.wink', 'name': 'Wink'},

    # iOS
    # {'identifier': 'com.ss.iphone.ugc.Aweme', 'name': '抖音'},
]

class Log:
    @staticmethod
    def send(msg):
        print('[Send] ' + msg)

    @staticmethod
    def println(msg):
        print(msg)

    @staticmethod
    def info(msg):
        print('[Info] ' + msg)

    @staticmethod
    def warn(msg):
        print('\033[0;33m[Warning] ' + msg + '\033[0m')

    @staticmethod
    def error(msg):
        print('\033[0;31m[Error] ' + msg + '\033[0m')

def on_message(message):
    if message['type'] == 'error':
        Log.error(message['description'])
        Log.error(message['stack'])
    else:
        Log.error(str(message))

unique_shader_hash = []
unique_texture_hash = []

def glFormat2PILFormat(glFormat):
    if glFormat == 'GL_RGBA':
        return 'RGBA'
    elif glFormat == 'GL_LUMINANCE':
        return 'L'
    return 'unsupported'

# 保存OpenGL shader source
def saveShader(payload):
    shader_source = payload.encode('utf8')
    source_hash = get_hash(shader_source)
    if source_hash not in unique_shader_hash:
        Log.send('Received shader source: ' + source_hash)
        unique_shader_hash.append(source_hash)
        with open(processes_to_hook[0]['identifier'] + '/shader/glShaderSource_' + source_hash + '.txt', 'wb') as f:
            f.write(shader_source)
            f.close()

# 尝试使用PIL处理SDR图像，例如GL_RGBA和GL_LUMINANCE
def saveSdrImageWithPil(param, data):
    pilFormat = glFormat2PILFormat(param['internalformat'])
    if pilFormat != 'unsupported':
        texture_hash = get_hash(data)
        if texture_hash not in unique_texture_hash:
            Log.send('Received SDR texture: ' + texture_hash + ', datalen: ' + str(len(data)))
            unique_texture_hash.append(texture_hash)
            rgba_image = Image.frombytes(pilFormat, (param['width'], param['height']), data)
            rgba_image.save(processes_to_hook[0]['identifier'] + '/texture/glTexImage2D_' + texture_hash + '.png', format='PNG')
        return True
    else:
        return False

# 尝试使用OpenEXR处理HDR图像
def saveHdrImageWithOpenExr(param, data):
    texture_hash = get_hash(data)
    if texture_hash not in unique_texture_hash:
        Log.send('Received HDR texture: ' + texture_hash + ', datalen: ' + str(len(data)))
        # 根据不同的格式确定数据类型和像素类型
        if param['internalformat'] == 'GL_RGBA32F':
            dtype = numpy.float32
            pixel_type = Imath.PixelType.FLOAT
            channels = 4
        elif param['internalformat'] == 'GL_RGBA16F':
            dtype = numpy.float16
            pixel_type = Imath.PixelType.HALF
            channels = 4
        elif param['internalformat'] == 'GL_RGB32F':
            dtype = numpy.float32
            pixel_type = Imath.PixelType.FLOAT
            channels = 3
        elif param['internalformat'] == 'GL_RGB16F':
            dtype = numpy.float16
            pixel_type = Imath.PixelType.HALF
            channels = 3
        else:
            Log.warn('Unsupported')
            return
        
        pixels = numpy.frombuffer(data, dtype=dtype)
        pixels = pixels.reshape((int(param['height']), int(param['width']), channels))
        if channels == 4:
            r = pixels[:, :, 0].flatten().astype(dtype).tobytes()
            g = pixels[:, :, 1].flatten().astype(dtype).tobytes()
            b = pixels[:, :, 2].flatten().astype(dtype).tobytes()
            a = pixels[:, :, 3].flatten().astype(dtype).tobytes()
            channel_names = "RGBA"
            channel_data = {'R': r, 'G': g, 'B': b, 'A': a}
        elif channels == 3:
            r = pixels[:, :, 0].flatten().astype(dtype).tobytes()
            g = pixels[:, :, 1].flatten().astype(dtype).tobytes()
            b = pixels[:, :, 2].flatten().astype(dtype).tobytes()
            channel_names = "RGB"
            channel_data = {'R': r, 'G': g, 'B': b}
        
        header = OpenEXR.Header(int(param['width']), int(param['height']))
        half_chan = Imath.Channel(Imath.PixelType(pixel_type))
        header['channels'] = dict([(c, half_chan) for c in channel_names])

        path = os.getcwd() + os.sep + processes_to_hook[0]['identifier'] + os.sep + 'texture' + os.sep + 'glTexImage2D_' + texture_hash + '.exr'

        out = OpenEXR.OutputFile(path, header)
        out.writePixels(channel_data)
        out.close()
        unique_texture_hash.append(texture_hash)

def on_gl_message(message, data):
    global unique_shader_hash
    global unique_texture_hash
    if message['type'] == 'send':
        payload = message['payload']
        if payload.startswith('glTexImage2D:'):
            if data == None or data == '':
                Log.println('data is None')
                return
            param = json.loads(payload.replace('glTexImage2D:', ''))
            if saveSdrImageWithPil(param, data):
                return
            if saveHdrImageWithOpenExr(param, data):
                return
        elif payload.startswith('glTexSubImage2D:'):
            if data == None or data == '':
                Log.println('data is None')
                return
            param = json.loads(payload.replace('glTexSubImage2D:', ''))
            if saveSdrImageWithPil(param, data):
                return
            if saveHdrImageWithOpenExr(param, data):
                return
        else:
            saveShader(payload)
    else:
        on_message(message)

# Full file name: hook_[platform]_[name].js
js_modules = [
    # {'platform': 'android', 'name': 'gl', 'on': on_gl_message},
    {'platform': 'android', 'name': 'asset', 'on': on_message},
    {'platform': 'android', 'name': 'mt', 'on': on_message},
]

def get_hash(data):
    hash = hashlib.sha256()
    hash.update(data)
    return hash.hexdigest()

def init_device():
    Log.info('Current frida version: '+str(frida.__version__))
    manager = frida.get_device_manager()
    Log.println('Select a frida device:')
    devices = manager.enumerate_devices()
    i = 0
    for ldevice in devices:
        i = i + 1
        Log.println(str(i) + ' => ' + str(ldevice))
    if i == 4:
        select = 4
        Log.warn('Auto select the only usb device...')
    elif i == 1 or i == 2:
        select = 1
        Log.warn('Auto select local system device...')
    else:
        select = int(input())
    if select > len(devices):
        Log.error('Out of range.')
        sys.exit(1)
    device_id = devices[select - 1].id
    
    device = manager.get_device(device_id, 1)
    Log.info('Connect to device \''+device.name+'\' successfully.')
    return device
    

if __name__ == '__main__':
    try:
        device = init_device()
        for per_hook_process in processes_to_hook:
            try:
                device.get_process(per_hook_process['name'])
            except frida.ProcessNotFoundError as e:
                Log.warn('Unable to find process \''+per_hook_process['name']+'\', try to spawn...')
                # Must use identifier to spawn
                try:
                    pid = device.spawn(per_hook_process['identifier'])
                    device.resume(pid)
                except frida.ExecutableNotFoundError as e2:
                    Log.error('Unable to find execuable \''+per_hook_process['name']+'\'.')
            
            session = device.attach(per_hook_process['name'])
            
            for js_module in js_modules:
                full_script_name = 'hook_' + js_module['platform'] + '_' + js_module['name'] + '.js'
                with open(full_script_name, 'rb') as f:
                    script = session.create_script(f.read().decode('utf8'))
                if 'on' in js_module:
                    script.on('message', js_module['on'])
                else:
                    script.on('message', on_message)
                Log.info('Load script name: ' + full_script_name)
                script.load()
        
        Log.info('Waiting for JavaScript...')
        print('--------------------------------------------------')
        sys.stdin.read()

    except Exception as e:
        Log.error(repr(e))

