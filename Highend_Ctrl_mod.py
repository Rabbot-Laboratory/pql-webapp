import threading
import serial
import socket
import base64
import json
import time
import sys  # For command-line arguments
from glob import glob
from serial.tools import list_ports

# --- デバッグ設定 ---
# デバッグレベル定義
DEBUG_LEVELS = {
    "NONE": 0,      # デバッグメッセージなし
    "ERROR": 1,     # エラーメッセージのみ
    "INFO": 2,      # 通常の情報メッセージ (ハンドラ実行など)
    "VERBOSE": 3,   # 詳細な情報メッセージ (ESP32送受信内容の概要など)
    "DATA_IO": 4    # 生データに近いI/O情報 (Base64、詳細なビット列など)
}
# 現在のデバッグレベル設定
CURRENT_DEBUG_LEVEL = DEBUG_LEVELS["DATA_IO"]

def dprint(level, *args, **kwargs):
    """指定されたレベルが現在のデバッグレベル以上の場合にメッセージを出力する"""
    if CURRENT_DEBUG_LEVEL >= level:
        level_name = next((k for k, v in DEBUG_LEVELS.items() if v == level), "UNKNOWN_LVL")
        print(f"[{level_name}]", *args, **kwargs)

# スレッドを停止するためのイベント
stop_event = threading.Event()

# --- 定数定義 ---
FORMAT_SENSOR_BASE = 5
FORMAT_GAIN_RESPONSE_BASE = 10
FORMAT_REQUEST_GAIN = 1
FORMAT_SET_GAIN_BASE = 10
FORMAT_REQUEST_CAPTURE = 50
FORMAT_SET_TARGET = 63
NUM_ESP32_CONTROLLED_ACTUATORS = 4 # 1台のESP32が制御するアクチュエータの数
MAX_ACTUATORS = 8 # システム全体のアクチュエータの最大数

class ESP32Communicator:
    def __init__(self, front_communicator, emulation_mode=False):
        self.front_communicator = front_communicator
        self.emulation_mode = emulation_mode
        self.serial_connections = {} # 辞書型に変更

        if self.emulation_mode:
            # --- Emulation Mode: Use UDP Sockets ---
            self.emu_listen_ip = "127.0.0.1"
            self.emu_listen_port = 6071
            self.emu_target_ip = "127.0.0.1"
            self.emu_target_port = 6070
            
            self.udp_send_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.udp_receive_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                self.udp_receive_socket.bind((self.emu_listen_ip, self.emu_listen_port))
                self.udp_receive_socket.settimeout(1.0)
                dprint(DEBUG_LEVELS["INFO"], f"[EMULATION MODE] Listening for emulator responses on UDP {self.emu_listen_ip}:{self.emu_listen_port}")
            except socket.error as e:
                dprint(DEBUG_LEVELS["ERROR"], f"[EMULATION MODE] Failed to bind UDP receive socket: {e}")
                raise
        else:
            # --- Real Hardware Mode: Use Named Serial Ports ---
            # find_serial_portsを削除し、指定された名前のポートに接続
            target_ports = {"Front": "/dev/ttyUSB-Front", "Back": "/dev/ttyUSB-Back"}
            
            for name, port in target_ports.items():
                try:
                    conn = serial.Serial(port, 115200, timeout=1)
                    self.serial_connections[name] = conn
                    dprint(DEBUG_LEVELS["INFO"], f"シリアルポート {port} ({name}) に接続しました。")
                except serial.SerialException as e:
                    dprint(DEBUG_LEVELS["ERROR"], f"警告: シリアルポート {port} ({name}) を開けませんでした: {e}")
            
            if not self.serial_connections:
                dprint(DEBUG_LEVELS["ERROR"], "エラー: ESP32のシリアルポートが一つも見つかりません。")
                raise ConnectionError("ESP32のシリアルポートが見つかりません。")
    
    def format_binary_data(self, int_data, bits=64):
        if int_data is None: return "N/A"
        try:
            bin_str = format(int_data, f'0{bits}b')
            id_part = bin_str[:6]
            data_part = bin_str[6:]
            return f"ID:0b{id_part} | Data:0b{data_part}"
        except Exception as e:
            dprint(DEBUG_LEVELS["ERROR"], f"2進数フォーマットエラー: {e}, data={int_data}")
            return f"FormatError (data={int_data})"

    def _process_received_line(self, raw_line, port_name="N/A"):
        """Helper function to process a single line of data from a source."""
        try:
            raw_data_b64 = raw_line.decode('ASCII').rstrip()
            if not raw_data_b64: return
            
            dprint(DEBUG_LEVELS["DATA_IO"], f"HIGHEND Recv from {port_name} (raw_b64): {raw_data_b64}")
            decoded_bytes = base64.b64decode(raw_data_b64)
            
            if len(decoded_bytes) != 8:
                dprint(DEBUG_LEVELS["ERROR"], f"エラー: {port_name}からのデータ長が8バイトではありません ({len(decoded_bytes)} bytes)。")
                return

            received_int_data = int.from_bytes(decoded_bytes, 'little')
            dprint(DEBUG_LEVELS["VERBOSE"], f"HIGHEND Recv from {port_name} (int): {received_int_data} ({self.format_binary_data(received_int_data)})")
            # port_nameを渡すように変更
            self.process_data(received_int_data, port_name)

        except (UnicodeDecodeError, base64.binascii.Error, ValueError) as e:
            dprint(DEBUG_LEVELS["ERROR"], f"ESP32データ処理エラー ({port_name}): {e}, 受信行: {raw_line.strip()}")
        except Exception as e:
            dprint(DEBUG_LEVELS["ERROR"], f"ESP32予期せぬデータ処理エラー ({port_name}): {e}, 受信行: {raw_line.strip()}")

    def read_data(self):
        while not stop_event.is_set():
            if self.emulation_mode:
                try:
                    raw_line, addr = self.udp_receive_socket.recvfrom(1024)
                    if raw_line:
                        # エミュレーションではポート名を固定
                        self._process_received_line(raw_line, port_name="Front")
                except socket.timeout:
                    continue
                except Exception as e:
                    dprint(DEBUG_LEVELS["ERROR"], f"[EMULATION MODE] UDP receive error: {e}")
                    time.sleep(0.1)
            else:
                # 辞書をループ
                for name, conn in self.serial_connections.items():
                    try:
                        if conn.is_open and conn.in_waiting > 0:
                            raw_line = conn.readline()
                            if raw_line:
                                # ポート名を渡す
                                self._process_received_line(raw_line, port_name=name)
                    except serial.SerialException as e:
                        dprint(DEBUG_LEVELS["ERROR"], f"シリアルポート {conn.port} ({name}) エラー: {e}")
                        stop_event.set()
                        break
                    except Exception as e:
                        dprint(DEBUG_LEVELS["ERROR"], f"ESP32データ読み取り中予期せぬエラー on port {conn.port} ({name}): {e}")
                
                if stop_event.is_set():
                    break
                
                time.sleep(0.001)

        # --- Cleanup ---
        if self.emulation_mode:
            self.udp_receive_socket.close()
            self.udp_send_socket.close()
            dprint(DEBUG_LEVELS["INFO"], "[EMULATION MODE] UDPソケットをクローズしました。")
        else:
            for name, conn in self.serial_connections.items():
                if conn and conn.is_open:
                    conn.close()
                    dprint(DEBUG_LEVELS["INFO"], f"シリアルポート {conn.port} ({name}) をクローズしました。")

    def process_data(self, data, port_name):
        parsed_data = self.parse_data(data)
        if parsed_data:
            dprint(DEBUG_LEVELS["VERBOSE"], f"HIGHEND Parsed ESP32 data from {port_name}: {parsed_data}")
            # port_nameを渡す
            json_data_to_front = self.front_communicator.convert_esp_data_to_json(parsed_data, port_name)
            if json_data_to_front:
                self.front_communicator.send_data_to_front(json_data_to_front)
        else:
            dprint(DEBUG_LEVELS["INFO"], f"ESP32データ解析結果がNoneでした。")

    def parse_data(self, data):
        format_value = (data >> 58) & 0x3F
        parsed_data = {'format': format_value}

        if format_value in [11, 21, 31, 41]:
            parsed_data.update({
                'p_gain': (data >> 50) & 0xFF,
                'i_gain': (data >> 42) & 0xFF,
                'd_gain': (data >> 34) & 0xFF,
                'capture_max': (data >> 22) & 0xFFF,
                'capture_min': (data >> 10) & 0xFFF
            })
        elif format_value >= FORMAT_SENSOR_BASE and format_value < FORMAT_SENSOR_BASE + NUM_ESP32_CONTROLLED_ACTUATORS:
            parsed_data.update({
                'position': (data >> 46) & 0xFFF,
                'voltage': (data >> 34) & 0xFFF,
                'command': (data >> 22) & 0xFFF
            })
        else:
            dprint(DEBUG_LEVELS["INFO"], f"未対応ESP32データフォーマットID: {format_value} (受信データ全体: {self.format_binary_data(data)})")
            return None
        return parsed_data

    def send_to_esp32(self, data_to_send_int, target_port_name):
        """指定されたポートにデータを送信する"""
        try:
            byte_data = data_to_send_int.to_bytes(8, 'big')
            b64_encoded_data = base64.b64encode(byte_data)
            data_to_write = b64_encoded_data + b'\n'
            
            dprint(DEBUG_LEVELS["VERBOSE"], f"HIGHEND Sending to {target_port_name} (int): {data_to_send_int} ({self.format_binary_data(data_to_send_int)})")
            dprint(DEBUG_LEVELS["DATA_IO"], f"HIGHEND Sending to {target_port_name} (b64): {data_to_write.strip()}")

            if self.emulation_mode:
                self.udp_send_socket.sendto(data_to_write, (self.emu_target_ip, self.emu_target_port))
                dprint(DEBUG_LEVELS["VERBOSE"], f"HIGHEND Sent to ESP32 emulator at {self.emu_target_ip}:{self.emu_target_port}")
            else:
                conn = self.serial_connections.get(target_port_name)
                if conn and conn.is_open:
                    try:
                        conn.write(data_to_write)
                        dprint(DEBUG_LEVELS["VERBOSE"], f"HIGHEND Sent to ESP32 via serial {conn.port} ({target_port_name})")
                    except serial.SerialException as e:
                        dprint(DEBUG_LEVELS["ERROR"], f"エラー: シリアルポート {conn.port} ({target_port_name}) への書き込みに失敗: {e}")
                else:
                    dprint(DEBUG_LEVELS["ERROR"], f"エラー: ESP32への送信時にシリアルポート {target_port_name} が利用できません。")
        except Exception as e:
            dprint(DEBUG_LEVELS["ERROR"], f"ESP32へのデータ送信エラー: {e}")


class FrontCommunicator:
    def __init__(self, esp32_communicator_instance, ip="0.0.0.0", port_send_to_front=6050, port_receive_from_front=6060):
        self.esp32_communicator = esp32_communicator_instance
        self.front_target_ip = "192.168.1.2" 
        self.udp_listen_ip = ip
        self.udp_port_send_to_front = port_send_to_front
        self.udp_port_receive_from_front = port_receive_from_front

        self.udp_send_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp_receive_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.udp_receive_socket.bind((self.udp_listen_ip, self.udp_port_receive_from_front))
            self.udp_receive_socket.settimeout(1.0)
            dprint(DEBUG_LEVELS["INFO"], f"フロントUDP受信用ポート {self.udp_listen_ip}:{self.udp_port_receive_from_front} で待機中...")
        except socket.error as e:
            dprint(DEBUG_LEVELS["ERROR"], f"エラー: UDP受信ポート {self.udp_listen_ip}:{self.udp_port_receive_from_front} のバインドに失敗: {e}")
            raise
        
        # MAX_ACTUATORS (8) に拡張
        self.actuator_states = {i: {"position": 2048, "voltage": 0, "command": 2048} for i in range(MAX_ACTUATORS)}
        
        self.fixed_motion_thread = None
        self.fix_inprocess_flag = False
        self.bulk_csv_thread = None
        self.bulk_csv_stop_event = threading.Event()

        self.request_handlers = {
            "set_target_value": self._handle_set_target_value,
            "request_gain_value": self._handle_request_gain_value,
            "request_capture": self._handle_request_capture,
            "set_gain_value": self._handle_set_gain_value,
            "request_gain_save": self._handle_request_gain_save,
            "fixed_motion": self._handle_fixed_motion,
            "set_target_values_bulk": self._handle_set_target_values_bulk,
            "stop_csv_playback": self._handle_stop_csv_playback,
        }

    def send_data_to_front(self, data_dict):
        try:
            json_string = json.dumps(data_dict)
            dprint(DEBUG_LEVELS["VERBOSE"], f"HIGHEND Sent to Front ({self.front_target_ip}:{self.udp_port_send_to_front}): {json_string}")
            self.udp_send_socket.sendto(json_string.encode('utf-8'), (self.front_target_ip, self.udp_port_send_to_front))
        except Exception as e:
            dprint(DEBUG_LEVELS["ERROR"], f"フロントエンドへのデータ送信エラー: {e}")

    def convert_esp_data_to_json(self, esp_data, port_name):
        fmt = esp_data.get('format')
        json_response = {"type": "unknown_response"}
        
        # port_nameが'Back'の場合、番号をオフセット
        num_offset = NUM_ESP32_CONTROLLED_ACTUATORS if port_name == 'Back' else 0

        if fmt >= FORMAT_SENSOR_BASE and fmt < FORMAT_SENSOR_BASE + NUM_ESP32_CONTROLLED_ACTUATORS:
            local_actuator_num = fmt - FORMAT_SENSOR_BASE
            global_actuator_num = local_actuator_num + num_offset
            
            if 0 <= global_actuator_num < MAX_ACTUATORS: 
                self.actuator_states[global_actuator_num] = {
                    "position": esp_data.get('position'),
                    "voltage": esp_data.get('voltage'),
                    "command": esp_data.get('command')
                }
                json_response = {
                    "type": "current_sensor_value",
                    "sensors": [{
                        "num": global_actuator_num, # マッピング後の番号を使用
                        "position": esp_data.get('position'),
                        "voltage": esp_data.get('voltage'),
                        "command": esp_data.get('command')
                    }]
                }
            else:
                dprint(DEBUG_LEVELS["INFO"], f"警告: センサーデータのアクチュエータ番号が範囲外です: {global_actuator_num}")
                return None

        elif fmt in [11, 21, 31, 41]: 
            actuator_num_map = {11: 0, 21: 1, 31: 2, 41: 3}
            local_actuator_num = actuator_num_map.get(fmt)
            if local_actuator_num is not None:
                global_actuator_num = local_actuator_num + num_offset
                json_response = {
                    "type": "response_gain_value",
                    "num": global_actuator_num, # マッピング後の番号を使用
                    "gains": {
                        "p": esp_data.get('p_gain'),
                        "i": esp_data.get('i_gain'),
                        "d": esp_data.get('d_gain'),
                    },
                    "capture": {
                        "max": esp_data.get('capture_max'),
                        "min": esp_data.get('capture_min'),
                    }
                }
            else:
                dprint(DEBUG_LEVELS["INFO"], f"ゲイン応答の未対応フォーマット: {fmt}")
                return None
        else:
            dprint(DEBUG_LEVELS["INFO"], f"フロントエンドJSONへの変換未対応のESP32フォーマット: {fmt}")
            return None
        return json_response

    def receive_data_from_front(self):
        dprint(DEBUG_LEVELS["INFO"], "フロントからのデータ受信スレッド開始...")
        while not stop_event.is_set():
            try:
                data, addr = self.udp_receive_socket.recvfrom(65535) 
                json_string = data.decode('utf-8')
                log_data = json_string[:300] + ('...' if len(json_string) > 300 else '')
                dprint(DEBUG_LEVELS["VERBOSE"], f"HIGHEND Recv from Front ({addr}): {log_data}")
                
                front_request = json.loads(json_string)
                request_type = front_request.get('type')
                self.front_target_ip = addr[0] 

                handler = self.request_handlers.get(request_type)
                if handler:
                    dprint(DEBUG_LEVELS["INFO"], f"ハンドラ実行: {request_type}")
                    handler(front_request)
                else:
                    dprint(DEBUG_LEVELS["INFO"], f"未対応フロントリクエストタイプ: {request_type}")

            except socket.timeout: 
                continue
            except json.JSONDecodeError as e:
                dprint(DEBUG_LEVELS["ERROR"], f"フロントJSONデコードエラー: {e}, データ: {json_string[:300]}...")
            except Exception as e:
                dprint(DEBUG_LEVELS["ERROR"], f"フロントデータ受信中予期せぬエラー: {e}")
                time.sleep(0.1) 
            
            if stop_event.is_set():
                break
        dprint(DEBUG_LEVELS["INFO"], "フロントからのUDP受信スレッド終了。")

    def _build_esp32_target_command(self, local_target_idx, target_value, is_position_control, state_offset):
        format_value = FORMAT_SET_TARGET
        bit_input_val = 0b0000 if is_position_control else 0b1111

        current_fields = [0] * NUM_ESP32_CONTROLLED_ACTUATORS
        for i in range(NUM_ESP32_CONTROLLED_ACTUATORS):
            global_idx = i + state_offset
            if is_position_control:
                current_fields[i] = self.actuator_states[global_idx]['position']
            else:
                current_fields[i] = self.actuator_states[global_idx]['command']
        
        if 0 <= local_target_idx < NUM_ESP32_CONTROLLED_ACTUATORS:
            current_fields[local_target_idx] = target_value
            global_target_idx = local_target_idx + state_offset
            if is_position_control:
                self.actuator_states[global_target_idx]['position'] = target_value
            else:
                self.actuator_states[global_target_idx]['command'] = target_value
        
        data_int = (format_value << 58) | \
                   (bit_input_val << 54) | \
                   (current_fields[0] << 42) | \
                   (current_fields[1] << 30) | \
                   (current_fields[2] << 18) | \
                   (current_fields[3] << 6)
        return data_int

    def _handle_set_target_value(self, request): 
        actuator_data = None
        is_pos_ctrl = False
        if 'position' in request and request['position']:
            actuator_data = request['position'][0]
            is_pos_ctrl = True
        elif 'command' in request and request['command']:
            actuator_data = request['command'][0]
            is_pos_ctrl = False
        
        if actuator_data:
            try:
                global_target_idx = int(actuator_data.get('num'))
                target_val = int(float(actuator_data.get('value')))

                if 0 <= global_target_idx < NUM_ESP32_CONTROLLED_ACTUATORS:
                    target_port = 'Front'
                    local_target_idx = global_target_idx
                    state_offset = 0
                elif NUM_ESP32_CONTROLLED_ACTUATORS <= global_target_idx < MAX_ACTUATORS:
                    target_port = 'Back'
                    local_target_idx = global_target_idx - NUM_ESP32_CONTROLLED_ACTUATORS
                    state_offset = NUM_ESP32_CONTROLLED_ACTUATORS
                else:
                    dprint(DEBUG_LEVELS["INFO"], f"スライダー制御: Act {global_target_idx} は制御範囲外(0-{MAX_ACTUATORS-1})。")
                    return

                data_int_to_esp32 = self._build_esp32_target_command(local_target_idx, target_val, is_pos_ctrl, state_offset)
                self.esp32_communicator.send_to_esp32(data_int_to_esp32, target_port)

            except (ValueError, TypeError) as e:
                dprint(DEBUG_LEVELS["ERROR"], f"set_target_value ({'pos' if is_pos_ctrl else 'cmd'}) 値無効: {actuator_data},エラー: {e}")
        else:
            dprint(DEBUG_LEVELS["INFO"], f"不正なset_target_valueリクエスト: {request}")


    def _handle_request_gain_value(self, request):
        try:
            actuator_num = int(request.get('num'))
            
            if 0 <= actuator_num < NUM_ESP32_CONTROLLED_ACTUATORS:
                target_port = 'Front'
                local_num = actuator_num
            elif NUM_ESP32_CONTROLLED_ACTUATORS <= actuator_num < MAX_ACTUATORS:
                target_port = 'Back'
                local_num = actuator_num - NUM_ESP32_CONTROLLED_ACTUATORS
            else:
                dprint(DEBUG_LEVELS["ERROR"], f"ゲイン要求: 無効なAct番号 {actuator_num}")
                return

            format_value = FORMAT_REQUEST_GAIN
            actuator_masks = {0: 0b1000, 1: 0b0100, 2: 0b0010, 3: 0b0001}
            field1_mask = actuator_masks.get(local_num)
            if field1_mask is None: return 
            data_int = (format_value << 58) | (field1_mask << 54)
            self.esp32_communicator.send_to_esp32(data_int, target_port)
        except (ValueError, TypeError) as e: 
            dprint(DEBUG_LEVELS["ERROR"], f"ゲイン要求値エラー: {request}, error: {e}")

    def _handle_request_capture(self, request):
        try:
            actuator_num = int(request.get('num'))
            capture_type = request.get('capture')

            if 0 <= actuator_num < NUM_ESP32_CONTROLLED_ACTUATORS:
                target_port = 'Front'
                local_num = actuator_num
            elif NUM_ESP32_CONTROLLED_ACTUATORS <= actuator_num < MAX_ACTUATORS:
                target_port = 'Back'
                local_num = actuator_num - NUM_ESP32_CONTROLLED_ACTUATORS
            else:
                dprint(DEBUG_LEVELS["ERROR"], f"キャプチャ要求: 無効なAct番号 {actuator_num}")
                return

            format_value = FORMAT_REQUEST_CAPTURE
            actuator_masks = {0: 0b1000, 1: 0b0100, 2: 0b0010, 3: 0b0001}
            field1_act_mask = actuator_masks.get(local_num)
            capture_type_map = {"offset": 0b01, "stroke": 0b10}
            field2_capture_type = capture_type_map.get(capture_type)
            if field1_act_mask is None or field2_capture_type is None: 
                dprint(DEBUG_LEVELS["ERROR"], f"キャプチャ要求: 無効なパラメータ {request}")
                return
            data_int = (format_value << 58) | (field1_act_mask << 54) | (field2_capture_type << 52)
            self.esp32_communicator.send_to_esp32(data_int, target_port)
        except (ValueError, TypeError) as e: 
            dprint(DEBUG_LEVELS["ERROR"], f"キャプチャ要求値エラー: {request}, error: {e}")

    def _handle_set_gain_value(self, request):
        try:
            actuator_num = int(request.get('num'))
            p_val, i_val, d_val = float(request.get('p')), float(request.get('i')), float(request.get('d'))

            if 0 <= actuator_num < NUM_ESP32_CONTROLLED_ACTUATORS:
                target_port = 'Front'
                local_num = actuator_num
            elif NUM_ESP32_CONTROLLED_ACTUATORS <= actuator_num < MAX_ACTUATORS:
                target_port = 'Back'
                local_num = actuator_num - NUM_ESP32_CONTROLLED_ACTUATORS
            else:
                dprint(DEBUG_LEVELS["ERROR"], f"set_gain_value: 無効なアクチュエータ番号 {actuator_num}")
                return
            
            format_value_map = {0: 10, 1: 20, 2: 30, 3: 40}
            format_value = format_value_map.get(local_num)
            if format_value is None:
                dprint(DEBUG_LEVELS["ERROR"], f"set_gain_value: 無効なローカルアクチュエータ番号 {local_num}")
                return

            p_bin = max(0, min(255, int(p_val)))
            i_bin = max(0, min(255, int(i_val)))
            d_bin = max(0, min(255, int(d_val)))
            data_int = (format_value << 58) | (p_bin << 50) | (i_bin << 42) | (d_bin << 34)
            self.esp32_communicator.send_to_esp32(data_int, target_port)
        except (ValueError, TypeError, KeyError) as e:
            dprint(DEBUG_LEVELS["ERROR"], f"set_gain_value パラメータ無効: {request}, エラー: {e}")

    def _handle_request_gain_save(self, request): 
        dprint(DEBUG_LEVELS["INFO"], f"ゲイン保存要求 (未実装): {request}")

    def _handle_fixed_motion(self, request): 
        dprint(DEBUG_LEVELS["INFO"], f"固定モーション要求 (現在未対応): {request}")

    def _handle_set_target_values_bulk(self, request):
        dprint(DEBUG_LEVELS["INFO"], f"CSV一括再生リクエスト受信: {str(request)[:200]}...")
        if self.bulk_csv_thread and self.bulk_csv_thread.is_alive():
            self.bulk_csv_stop_event.set()
            dprint(DEBUG_LEVELS["INFO"], "前回のCSV再生スレッド停止要求中...")
            self.bulk_csv_thread.join(timeout=1.0)
            self.bulk_csv_thread = None
        
        self.bulk_csv_stop_event.clear()
        interval = request.get("interval", 1.0 / 30.0)
        csv_data_rows = request.get("data", [])
        loop_playback = request.get("loop", False) 

        if not csv_data_rows:
            dprint(DEBUG_LEVELS["INFO"], "一括CSVデータ空。")
            return

        dprint(DEBUG_LEVELS["INFO"], f"CSV一括再生開始: {len(csv_data_rows)}行, 間隔: {interval}秒, ループ: {loop_playback}")
        self.send_data_to_front({"type": "csv_playback_started_ack"}) 

        self.bulk_csv_thread = threading.Thread(
            target=self._playback_bulk_csv_data, 
            args=(interval, csv_data_rows, loop_playback), 
            name="BulkCSVPlaybackThread"
        )
        self.bulk_csv_thread.daemon = True
        self.bulk_csv_thread.start()

    def _handle_stop_csv_playback(self, request):
        dprint(DEBUG_LEVELS["INFO"], "CSV再生停止リクエスト受信。")
        if self.bulk_csv_thread and self.bulk_csv_thread.is_alive():
            self.bulk_csv_stop_event.set()
            dprint(DEBUG_LEVELS["INFO"], "CSV一括再生スレッドに停止イベントセット。")
        else:
            dprint(DEBUG_LEVELS["INFO"], "停止対象CSV再生スレッドなし。")

    def _playback_bulk_csv_data(self, interval, csv_rows, loop):
        thread_name = threading.current_thread().name
        dprint(DEBUG_LEVELS["INFO"], f"スレッド ({thread_name}) 開始: {len(csv_rows)}行, ループ={loop}")
        
        while not stop_event.is_set(): 
            playback_interrupted = False
            for row_index, row_data_list in enumerate(csv_rows):
                if stop_event.is_set() or self.bulk_csv_stop_event.is_set():
                    dprint(DEBUG_LEVELS["INFO"], f"スレッド ({thread_name}): 行 {row_index} で停止イベントにより中断。")
                    playback_interrupted = True
                    break

                front_fields = [self.actuator_states[i]['position'] for i in range(NUM_ESP32_CONTROLLED_ACTUATORS)]
                back_fields = [self.actuator_states[i + NUM_ESP32_CONTROLLED_ACTUATORS]['position'] for i in range(NUM_ESP32_CONTROLLED_ACTUATORS)]
                has_data_for_front = False
                has_data_for_back = False

                for csv_col_idx, value_str in enumerate(row_data_list):
                    if str(value_str).strip() == "": continue 
                    if csv_col_idx >= MAX_ACTUATORS: continue

                    try:
                        target_val = int(float(value_str))
                        if 0 <= csv_col_idx < NUM_ESP32_CONTROLLED_ACTUATORS:
                            local_idx = csv_col_idx
                            front_fields[local_idx] = target_val
                            self.actuator_states[local_idx]['position'] = target_val
                            has_data_for_front = True
                        else: # 4 <= csv_col_idx < 8
                            local_idx = csv_col_idx - NUM_ESP32_CONTROLLED_ACTUATORS
                            back_fields[local_idx] = target_val
                            self.actuator_states[csv_col_idx]['position'] = target_val
                            has_data_for_back = True
                    except ValueError:
                        dprint(DEBUG_LEVELS["ERROR"], f"CSVデータ値エラー 行 {row_index}, 列 {csv_col_idx}: '{value_str}'")
                        self.bulk_csv_stop_event.set()
                        playback_interrupted = True
                        break
                
                if playback_interrupted: break

                if has_data_for_front:
                    data_to_front_esp = self._build_command_from_fields(front_fields)
                    self.esp32_communicator.send_to_esp32(data_to_front_esp, 'Front')
                
                if has_data_for_back:
                    data_to_back_esp = self._build_command_from_fields(back_fields)
                    self.esp32_communicator.send_to_esp32(data_to_back_esp, 'Back')

                time.sleep(interval)
            
            if playback_interrupted or not loop: 
                break 
            
            dprint(DEBUG_LEVELS["INFO"], f"スレッド ({thread_name}): 1サイクル完了。ループ再生のため再度実行。")

        if self.bulk_csv_stop_event.is_set():
            dprint(DEBUG_LEVELS["INFO"], f"スレッド ({thread_name}): CSV再生がリクエストにより停止。")
        else:
            dprint(DEBUG_LEVELS["INFO"], f"スレッド ({thread_name}): CSV再生正常終了。")
        
        dprint(DEBUG_LEVELS["INFO"], f"スレッド ({thread_name}) 実行終了。")
        self.send_data_to_front({"type": "csv_playback_finished"})

    def _build_command_from_fields(self, fields, is_position_control=True):
        format_value = FORMAT_SET_TARGET
        bit_input_val = 0b0000 if is_position_control else 0b1111
        return (format_value << 58) | \
               (bit_input_val << 54) | \
               (fields[0] << 42) | \
               (fields[1] << 30) | \
               (fields[2] << 18) | \
               (fields[3] << 6)
    
    # crawl_motion and its helper are not adapted for the new dual-ESP32 setup.
    # They would need significant changes to control actuators across both devices.
    def crawl_motion(self):
        dprint(DEBUG_LEVELS["INFO"], "Crawl motion is currently not supported in dual-ESP32 mode.")

    def _build_esp32_target_command_for_motion(self, f1, f2, f3, f4):
        pass # Not implemented for dual setup


class MainApp:
    def __init__(self, emulation_mode=False):
        self.esp32_comm = None
        self.front_comm = None
        try:
            self.esp32_comm = ESP32Communicator(None, emulation_mode=emulation_mode)
            self.front_comm = FrontCommunicator(self.esp32_comm)
            self.esp32_comm.front_communicator = self.front_comm
        except Exception as e:
            dprint(DEBUG_LEVELS["ERROR"], f"初期化エラー: {e}")
            raise

    def run(self):
        if not self.esp32_comm or not self.front_comm:
            dprint(DEBUG_LEVELS["ERROR"],"通信モジュール未初期化。実行不可。")
            return
        dprint(DEBUG_LEVELS["INFO"], "アプリケーション実行開始...")
        threads = []
        threads.append(threading.Thread(target=self.esp32_comm.read_data, name="ESP32ReceiverThread", daemon=True))
        threads.append(threading.Thread(target=self.front_comm.receive_data_from_front, name="FrontReceiverThread", daemon=True))
        
        for t in threads: t.start()
        try:
            while not stop_event.is_set():
                time.sleep(0.5)
        except KeyboardInterrupt:
            dprint(DEBUG_LEVELS["INFO"], "\nCtrl+C検出。終了処理...")
        finally:
            self.stop()
            dprint(DEBUG_LEVELS["INFO"], "メインスレッド: スレッド終了待機...")
            for t in threads:
                if t.is_alive():
                    t.join(timeout=1.0)
            if self.front_comm and self.front_comm.bulk_csv_thread and self.front_comm.bulk_csv_thread.is_alive():
                self.front_comm.bulk_csv_thread.join(timeout=1.0)
            dprint(DEBUG_LEVELS["INFO"], "全スレッド終了確認。")

    def stop(self):
        dprint(DEBUG_LEVELS["INFO"], "停止シグナル送信...")
        stop_event.set()
        if self.front_comm:
            self.front_comm.bulk_csv_stop_event.set()

if __name__ == "__main__":
    app = None
    try:
        emulation_mode = '--emulate' in sys.argv or '--emulation' in sys.argv
        
        if "--debug-io" in sys.argv: 
            CURRENT_DEBUG_LEVEL = DEBUG_LEVELS["DATA_IO"]
        elif "--debug-info" in sys.argv:
            CURRENT_DEBUG_LEVEL = DEBUG_LEVELS["INFO"]

        if emulation_mode:
            dprint(DEBUG_LEVELS["INFO"], "***** EMULATION MODE ENABLED *****")
        
        dprint(DEBUG_LEVELS["INFO"], f"現在のデバッグレベル: {CURRENT_DEBUG_LEVEL} ({[k for k,v in DEBUG_LEVELS.items() if v == CURRENT_DEBUG_LEVEL][0]})")

        app = MainApp(emulation_mode=emulation_mode)
        app.run()
    except (ConnectionError, serial.SerialException, socket.error) as e:
        dprint(DEBUG_LEVELS["ERROR"], f"アプリ起動失敗: {e}")
    except Exception as e:
        dprint(DEBUG_LEVELS["ERROR"], f"予期せぬエラー: {e}", exc_info=True)
    finally:
        if app and not stop_event.is_set():
            app.stop()
        dprint(DEBUG_LEVELS["INFO"], "アプリケーション終了。")
