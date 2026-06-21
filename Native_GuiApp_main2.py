import os
import csv # For CSV handling
import threading
import time
import json
import socket

# GUIライブラリのインポート (GUI Library Imports)
from kivymd.app import MDApp
from kivy.lang import Builder
from kivy.config import Config
from kivy.core.window import Window
Config.set('graphics', 'maxfps', 60)

# GUIコンポーネント関連 (GUI Component Related)
from kivymd.uix.card import MDCard
from kivymd.uix.button import MDButton, MDIconButton, MDButtonText
from kivymd.uix.snackbar import MDSnackbar, MDSnackbarText
from kivymd.uix.navigationdrawer import MDNavigationDrawerItem, MDNavigationDrawerItemText
from kivymd.uix.dialog import MDDialog, MDDialogHeadlineText, MDDialogContentContainer, MDDialogButtonContainer
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.textfield import MDTextField
from kivymd.uix.label import MDLabel
from kivy.uix.widget import Widget # MDDialogButtonContainerのスペーサー用

# Kivyプロパティ (Kivy Properties)
from kivy.properties import StringProperty, ListProperty, BooleanProperty, NumericProperty, ObjectProperty

# Kivyフォント関連 (Kivy Font Related)
from kivy.core.text import LabelBase, DEFAULT_FONT
fontdir = os.path.join(os.path.dirname(__file__), 'font', 'NotoSansJP-Regular.ttf')
rootdir = os.path.dirname(__file__)
LabelBase.register(DEFAULT_FONT, fontdir)

# グラフ描画関連 (Graph Drawing Related)
import numpy as np
from kivy.clock import Clock
from kivy_garden.matplotlib.backend_kivyagg import FigureCanvasKivyAgg
import matplotlib
matplotlib.use('Agg')   # 非アクティブになる現象を抑止 (Suppress inactive phenomenon)
import matplotlib.pyplot as plt
from matplotlib import font_manager
font_manager.fontManager.addfont(fontdir) #matplotlib
plt.rc('font', family="Noto Sans JP")

# 画面遷移関連 (Screen Transition Related)
from kivymd.uix.screen import MDScreen
from kivymd.uix.screenmanager import MDScreenManager
from functools import partial

# スレッドイベント (Threading Events)
stop_event = threading.Event() # アプリ全体の通信停止用
csv_stop_event = threading.Event() # CSV再生のUI制御や停止リクエスト用 (再生ループ自体はバックエンド)

class StartScreen(MDScreen):
    def on_connect_button_press(self):
        app = MDApp.get_running_app()
        app.start_communication()

class MainScreen(MDScreen):
    def on_disconnect_button_press(self):
        app = MDApp.get_running_app()
        app.stop_communication("Disconnected!")

class PageManager(MDScreenManager):
    pass
    
class NativeGUIApp(MDApp):
    Builder.load_file(os.path.join(rootdir, 'layout.kv'))
    trans_data =  '' 
    selected_actuater = NumericProperty(0)
    add_cylinder_num = 0 
    
    # CSV Playback Properties
    csv_file_path = StringProperty("")
    loaded_csv_filename = StringProperty("No CSV loaded")
    csv_data = ListProperty([]) # [[row1_col1, row1_col2,...], [row2_col1,...]]
    is_csv_playing = BooleanProperty(False)
    loop_csv = BooleanProperty(False) # ループ再生はまだmain2.py側で未対応だがUIは残す
    # current_csv_row_index = NumericProperty(0) # バックエンドで管理するため不要に
    # csv_playback_thread = ObjectProperty(None, allownone=True) # フロントでの再生スレッドは不要に
    _file_path_input_dialog = ObjectProperty(None, allownone=True)

    # Slider properties
    slider_position = StringProperty("2000") 
    slider_command = StringProperty("2000")  
    before_slider_position = StringProperty("2000")
    before_slider_command = StringProperty("2000")

    # Sensor data
    position = 0 
    voltage = 0  
    command = 0
    p = 0.0 
    i = 0.0
    d = 0.0
    capture_max = None
    capture_min = None
    
    def build(self):    
        self.screen_manager = PageManager()
        self.settings_manager = SettingsManager()
        
        self.theme_cls.theme_style_switch_animation = True
        self.theme_cls.theme_style = self.settings_manager.get_setting('theme', 'Light')
        self.theme_cls.primary_palette = self.settings_manager.get_setting('color', 'Green')
        self.actuater_name = self.settings_manager.get_setting('actuater_name', {})
        try:
            self.selected_actuater = int(self.settings_manager.get_setting('selected_actuater_default', '0'))
        except ValueError:
            self.selected_actuater = 0

        self.loop_csv = self.settings_manager.get_setting('csv_loop_enabled', False)
        last_path = self.settings_manager.get_setting('last_csv_path', "")
        if last_path and os.path.exists(last_path):
            self.csv_file_path = last_path
            self.loaded_csv_filename = os.path.basename(last_path)
        else:
            self.csv_file_path = ""
            self.loaded_csv_filename = "No CSV loaded"

        self.screen_manager.get_screen('start').ids.address_field.text = self.settings_manager.get_setting('address', 'localhost')
        self.connect_button = self.screen_manager.get_screen('start').ids.connect_button
        self.address_field = self.screen_manager.get_screen('start').ids.address_field
        self.progressindicator = self.screen_manager.get_screen('start').ids.progressindicator
        
        main_screen_ids = self.screen_manager.get_screen('main').ids
        self.graph_area = main_screen_ids.graph_area
        self.input_switch = main_screen_ids.input_switch
        self.position_switch = main_screen_ids.position_switch
        self.voltage_switch = main_screen_ids.voltage_switch
        self.command_switch = main_screen_ids.command_switch
        self.navigation_drawer = main_screen_ids.nav_drawer_menu

        self.gain_reload = main_screen_ids.gain_reload
        self.p_field = main_screen_ids.gain_p
        self.i_field = main_screen_ids.gain_i
        self.d_field = main_screen_ids.gain_d
        self.gain_send = main_screen_ids.gain_send
        self.save_button = main_screen_ids.gain_save
        self.position_slider = main_screen_ids.position_slider
        self.command_slider = main_screen_ids.command_slider
        self.offset_capture = main_screen_ids.offset_capture
        self.stroke_capture = main_screen_ids.stroke_capture
        
        self.play_stop_csv_button = main_screen_ids.play_stop_csv_button
        self.loop_csv_switch = main_screen_ids.loop_csv_switch
        self.loop_csv_switch.active = self.loop_csv 
        self.loaded_csv_filename_label = main_screen_ids.loaded_csv_filename_label
        self.loaded_csv_filename_label.text = self.loaded_csv_filename
        
        self.udp_thread = None        
        return self.screen_manager
    
    def on_start(self): 
        Window.maximize()
        self.screen_manager.get_screen('main').ids.nav_drawer.set_state("open")
        self.fig, self.ax = plt.subplots()

        if self.csv_file_path and os.path.exists(self.csv_file_path):
            self._load_csv_data(self.csv_file_path)
        else: 
            self.play_stop_csv_button.disabled = True
            self.loaded_csv_filename = "No CSV loaded"
            if hasattr(self, 'loaded_csv_filename_label'): 
                 self.loaded_csv_filename_label.text = self.loaded_csv_filename
    
    def on_stop(self):  
        stop_event.set() # アプリ全体の通信停止
        csv_stop_event.set() # CSV再生関連のUI操作やリクエスト送信を止める
        
        # バックエンドにCSV再生停止を通知 (もし再生中なら)
        if self.is_csv_playing and hasattr(self, 'dynamicUdpSocket') and self.dynamicUdpSocket:
            self.send_stop_csv_playback_request()

        if self.udp_thread and self.udp_thread.is_alive():
            self.udp_thread.join(timeout=0.5)
        self.settings_manager.save_settings() 
        return True
    
    def start_communication(self):
        address_field = self.screen_manager.get_screen('start').ids.address_field
        self.address = address_field.text
        try:
            # 送信用ソケット (宛先は main2.py の受信用ポート)
            self.dynamicUdpSocket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            # 受信用ソケットは udp_receiver スレッド内で作成・バインド
        except socket.error as e:
            self.show_snackbar(f"Socket creation error: {e}")
            return
        
        self.udp_thread = None 
        stop_event.clear() 
        csv_stop_event.clear() # 新規接続時にクリア

        if not self.udp_thread: 
            self.udp_thread = threading.Thread(target=self.udp_receiver, args=(self.address,))
            self.udp_thread.daemon = True 
            self.udp_thread.start()

    def stop_communication(self, message):
        if self.is_csv_playing: 
            # バックエンドにCSV再生停止を通知
            self.send_stop_csv_playback_request()
            self._update_csv_playback_ui_on_stop() # UIを即時更新

        self.change_screen('start')
        self.connect_button.disabled = False
        self.address_field.disabled = False
        self.progressindicator.active = False
        self.position_slider.disabled = True
        self.command_slider.disabled = True
        self.offset_capture.disabled = True
        self.stroke_capture.disabled = True
        self.gain_reload.disabled = True
        self.switch_gain_window(True)

        if hasattr(self, 'update_event') and self.update_event: 
            Clock.unschedule(self.update_event)
            self.update_event = None
        
        if self.navigation_drawer and self.navigation_drawer.children:
            try:
                if hasattr(self.navigation_drawer.children[0], 'clear_widgets'):
                     self.navigation_drawer.children[0].clear_widgets()
            except IndexError:
                self.navigation_drawer.clear_widgets()

        if hasattr(self.graph_area, 'clear_widgets'): 
            self.graph_area.clear_widgets() 
        
        self.selected_actuater = 0
        self.add_cylinder_num = 0 
        
        stop_event.set() 
        if hasattr(self, 'dynamicUdpSocket') and self.dynamicUdpSocket:
            try:
                self.dynamicUdpSocket.close()
            except Exception as e:
                print(f"Error closing dynamicUdpSocket: {e}")
            self.dynamicUdpSocket = None 
        
        # udp_socket (受信用) は udp_receiver スレッド内でクローズされる
        Clock.schedule_once(lambda dt: self.show_snackbar(message)) 
    
    def udp_receiver(self,address_arg): 
        self.settings_manager.update_setting('address', self.address_field.text)
        Clock.schedule_once(lambda dt: setattr(self.connect_button, 'disabled', True))
        Clock.schedule_once(lambda dt: setattr(self.address_field, 'disabled', True))
        Clock.schedule_once(lambda dt: setattr(self.progressindicator, 'active', True))
        print("UDPサーバーに接続中")

        udp_receive_socket = None # このスレッド専用の受信用ソケット
        try:
            udp_receive_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            udp_receive_socket.settimeout(3.0)
            udp_receive_socket.bind(("0.0.0.0", 6050)) # main2.py が送信してくるポート
            print("UDPでサーバーからのメッセージを受信中 (ポート6050)...")
            
            initial_data_received_flag = False
            # 初回データ受信トライ (接続確認)
            # main2.py は接続直後に何か送ってくるわけではないので、
            # ここでは接続試行が成功した時点で画面遷移する。
            # 最初のセンサーデータは main2.py が定期的に送ってくるのを待つ。
            
            # 接続成功とみなし、画面遷移
            print(f"Connected to backend at {self.address}") # 接続先は self.address (main2.py のIP)
            Clock.schedule_once(lambda x: self.change_screen('main'))
            Clock.schedule_once(lambda dt: setattr(self.progressindicator, 'active', False))

            current_num_for_drawer = 0 
                
            while not stop_event.is_set():
                try:              
                    data, addr = udp_receive_socket.recvfrom(4096) # main2.py からのデータ
                except socket.timeout:
                    if stop_event.is_set(): break
                    continue
                except Exception as e: 
                    if not stop_event.is_set():
                        print(f"UDP recv error during operation: {e}")
                    break 
                
                receive_data_str = data.decode('utf-8', errors='ignore')
                try:
                    received_json = json.loads(receive_data_str)
                except json.JSONDecodeError as je:
                    print(f"JSON Decode Error (loop): {je} - Data: '{receive_data_str}'")
                    continue 
                
                msg_type = received_json.get('type')
                if msg_type == "current_sensor_value":
                    sensors_data = received_json.get("sensors", [])
                    if sensors_data:
                        first_sensor_num = sensors_data[0].get("num")
                        if first_sensor_num is not None and first_sensor_num == current_num_for_drawer:
                            Clock.schedule_once(lambda dt: self.update_drawer_menu())
                            current_num_for_drawer += 1
                        
                        self.position = next((s.get('position') for s in sensors_data if s.get('num') == self.selected_actuater), self.position)
                        self.voltage = next((s.get('voltage') for s in sensors_data if s.get('num') == self.selected_actuater), self.voltage)
                        self.command = next((s.get('command') for s in sensors_data if s.get('num') == self.selected_actuater), self.command)

                elif msg_type == "response_gain_value":
                    cylinder_num_from_json = received_json.get("num")
                    if str(cylinder_num_from_json) == str(self.selected_actuater): 
                        gains = received_json.get("gains", {})
                        self.p = gains.get("p", self.p) 
                        self.i = gains.get("i", self.i)
                        self.d = gains.get("d", self.d)
                        self.capture_max = received_json.get("capture", {}).get("max", self.capture_max)
                        self.capture_min = received_json.get("capture", {}).get("min", self.capture_min)
                        Clock.schedule_once(lambda x: self.gain_sync(self.p, self.i, self.d))
                
                # --- CSV再生関連のメッセージ処理 ---
                elif msg_type == "csv_playback_finished":
                    Clock.schedule_once(lambda dt: self.show_snackbar("CSV playback finished by backend."))
                    Clock.schedule_once(lambda dt: self._update_csv_playback_ui_on_stop(finished=True))
                
                elif msg_type == "csv_playback_stopped_ack":
                    Clock.schedule_once(lambda dt: self.show_snackbar("CSV playback stopped by backend."))
                    # UI更新は既に toggle_csv_playback や stop_communication で行われているはず
                    # 必要であればここでさらに強制更新
                    if self.is_csv_playing: # まだ再生中フラグが立っていたら強制的に倒す
                         Clock.schedule_once(lambda dt: self._update_csv_playback_ui_on_stop(finished=False))


                elif msg_type == "csv_playback_error":
                    error_message = received_json.get("message", "Unknown CSV playback error from backend.")
                    Clock.schedule_once(lambda dt, msg=error_message: self.show_snackbar(msg))
                    Clock.schedule_once(lambda dt: self._update_csv_playback_ui_on_stop(finished=False)) # エラー時もUIを停止状態に

            if udp_receive_socket: 
                udp_receive_socket.close()
            udp_receive_socket = None 
            print("切断しました (UDP receiver stopped)")
        except Exception as e:
            error_message = f"UDP Communication Error: {e}"
            print(error_message)
            if not stop_event.is_set():
                 Clock.schedule_once(lambda x, emsg=error_message: self.stop_communication(emsg))
        finally:
            if udp_receive_socket:
                udp_receive_socket.close()
            Clock.schedule_once(lambda dt: setattr(self.connect_button, 'disabled', False))
            Clock.schedule_once(lambda dt: setattr(self.address_field, 'disabled', False))
            Clock.schedule_once(lambda dt: setattr(self.progressindicator, 'active', False))


    def send_udp_data(self, data_dict):
        """ main2.py にデータを送信する共通関数 """
        if hasattr(self, 'dynamicUdpSocket') and self.dynamicUdpSocket and self.address:
            try:
                self.dynamicUdpSocket.sendto(json.dumps(data_dict).encode('utf-8'), (self.address, 6060)) # main2.pyの受信用ポート
                print(f"Sent to backend: {data_dict}")
            except socket.error as e:
                self.show_snackbar(f"UDP Send Error: {e}")
                print(f"UDP Send Error: {e} when sending {data_dict}")
            except Exception as e:
                print(f"Error sending data to backend: {e} for data {data_dict}")
        else:
            self.show_snackbar("Not connected to backend.")
            print("Cannot send data, not connected to backend.")

    def change_screen(self, screen_name):
        self.root.current = screen_name
    
    def switch_gain_window(self, switch):
        self.p_field.disabled = switch
        self.i_field.disabled = switch
        self.d_field.disabled = switch
        self.gain_send.disabled = switch
        self.save_button.disabled = switch
        
    def gain_request(self):
        self.switch_gain_window(True)
        data = {"type": "request_gain_value", "num": self.selected_actuater}
        self.send_udp_data(data)

    def gain_sync(self,p_val,i_val,d_val):
        self.p_field.text = str(p_val if p_val is not None else "")
        self.i_field.text = str(i_val if i_val is not None else "")
        self.d_field.text = str(d_val if d_val is not None else "")
        self.switch_gain_window(False)
        
    def fixed_motion(self,motion_type):  
        data = {"type": "fixed_motion", "motion": motion_type}
        self.send_udp_data(data)
        self.show_snackbar(f"Fixed motion requesting... ⇒ {motion_type}")
        
    def gain_change(self):
        try:
            p_val = float(self.p_field.text)
            i_val = float(self.i_field.text)
            d_val = float(self.d_field.text)
        except ValueError:
            self.show_snackbar("Invalid gain values.")
            return
        self.switch_gain_window(True)
        data = {"type": "set_gain_value", "num": self.selected_actuater, "p": p_val, "i": i_val, "d": d_val}
        self.send_udp_data(data)
        self.show_snackbar(f"Gain change requesting...")
        
    def gain_save(self):
        self.switch_gain_window(True)
        data = {"type":"request_gain_save","num":self.selected_actuater}
        self.send_udp_data(data)
        self.show_snackbar(f"Gain save requesting...")
        
    def req_capture(self,capture_type_arg):
        data = {"type":"request_capture","num":self.selected_actuater, "capture": capture_type_arg}
        self.send_udp_data(data)
        self.show_snackbar(f"Capture requesting... ⇒  {capture_type_arg}")

    def switch_actuater(self, num_str_arg, obj):
        try:
            num_to_switch = int(num_str_arg)
        except ValueError:
            print(f"Invalid actuator number string: {num_str_arg}")
            return

        if self.selected_actuater != num_to_switch:
            self.selected_actuater = num_to_switch 
            self.settings_manager.update_setting('selected_actuater_default', str(self.selected_actuater))
            self.gain_request() 
            
            self.position_slider.disabled = False
            self.command_slider.disabled = False
            self.offset_capture.disabled = False
            self.stroke_capture.disabled = False
            self.gain_reload.disabled = False
            
            current_pos = self.position if self.position is not None else 2000
            current_cmd = self.command if self.command is not None else 2000
            
            self.screen_manager.get_screen('main').ids.position_slider.value = current_pos
            self.screen_manager.get_screen('main').ids.command_slider.value = current_cmd
            
            self.slider_position = str(current_pos)
            self.slider_command = str(current_cmd)
            self.before_slider_position = self.slider_position 
            self.before_slider_command = self.slider_command
            
            if hasattr(self, 'update_event') and self.update_event: 
                Clock.unschedule(self.update_event)
                self.update_event = None
            if hasattr(self.graph_area, 'clear_widgets'):
                self.graph_area.clear_widgets() 
            
            self.fig, self.ax = plt.subplots()

            if self.theme_cls.theme_style == "Dark":
                self.ax.spines['top'].set_color('white')
                self.ax.spines['bottom'].set_color('white')
                self.ax.spines['left'].set_color('white')
                self.ax.spines['right'].set_color('white')
                self.ax.tick_params(axis='y', colors='white')
                self.ax.tick_params(axis='x', colors='white')
            else: 
                self.ax.spines['top'].set_color('black')
                self.ax.spines['bottom'].set_color('black')
                self.ax.spines['left'].set_color('black')
                self.ax.spines['right'].set_color('black')
                self.ax.tick_params(axis='y', colors='black')
                self.ax.tick_params(axis='x', colors='black')

            self.fig.patch.set_alpha(0)
            self.ax.patch.set_alpha(0)
            
            self.x = list(range(200)) 
            self.y1 = [np.nan] * 200 
            self.y2 = [np.nan] * 200
            self.y3 = [np.nan] * 200
            self.y4 = [np.nan] * 200
            self.y5 = [np.nan] * 200
            
            self.pos_line, = self.ax.plot(self.x, self.y1, label="Position") 
            self.vol_line, = self.ax.plot(self.x, self.y2, label="Voltage")  
            self.com_line, = self.ax.plot(self.x, self.y3, label="Command")  
            self.target_pos_line, = self.ax.plot(self.x, self.y4, label="Target>Position") 
            self.target_com_line, = self.ax.plot(self.x, self.y5, label="Target>Command") 
            
            self.fig.legend()
            self.ax.set_ylim(0, 4095)
            self.ax.get_xaxis().set_visible(False) 
            
            if hasattr(self.graph_area, 'add_widget'):
                self.graph_area.add_widget(FigureCanvasKivyAgg(self.fig))   
            
            self.update_event = Clock.schedule_interval(self.loop_30fps, 1/30.0)
        
    def loop_30fps(self, *args):
        if not (hasattr(self, 'ax') and self.ax and hasattr(self, 'fig') and self.fig):
            return

        self.y1.append(self.position if self.position_switch.active else np.nan)
        self.y1.pop(0)
        self.y2.append(self.voltage if self.voltage_switch.active else np.nan)
        self.y2.pop(0)
        self.y3.append(self.command if self.command_switch.active else np.nan)
        self.y3.pop(0)
        
        try:
            slider_pos_val = int(float(self.slider_position))
        except (ValueError, TypeError):
            slider_pos_val = np.nan
        self.y4.append(slider_pos_val if self.position_switch.active and self.input_switch.active else np.nan)
        self.y4.pop(0)
        
        try:
            slider_cmd_val = int(float(self.slider_command))
        except (ValueError, TypeError):
            slider_cmd_val = np.nan
        self.y5.append(slider_cmd_val if self.command_switch.active else np.nan)
        self.y5.pop(0)

        if hasattr(self, 'pos_line') and self.pos_line.axes: 
            self.pos_line.set_ydata(self.y1)
            self.vol_line.set_ydata(self.y2)
            self.com_line.set_ydata(self.y3)
            self.target_pos_line.set_ydata(self.y4)
            self.target_com_line.set_ydata(self.y5)
            self.ax.relim()  
            self.ax.autoscale_view()  
            if hasattr(self.fig, 'canvas') and self.fig.canvas:
                try:
                    self.fig.canvas.draw()
                    self.fig.canvas.flush_events()
                    
                except Exception as e:
                    print(f"Error during canvas draw: {e}") 
        
        # ターゲット送信 (スライダー操作時)
        # CSV再生中はスライダーが無効なので、この部分はCSV再生とは独立して動作
        if not self.is_csv_playing: # CSV再生中でない場合のみスライダーからの送信を許可
            if hasattr(self, 'dynamicUdpSocket') and self.dynamicUdpSocket:
                try:
                    data_to_send = None
                    if self.before_slider_position != self.slider_position:
                        data_to_send = {"type":"set_target_value","position":[{"num":str(self.selected_actuater),"value":self.slider_position}]}
                        self.before_slider_position = self.slider_position 
                    elif self.before_slider_command != self.slider_command: 
                        data_to_send = {"type":"set_target_value","command":[{"num":str(self.selected_actuater),"value":self.slider_command}]}
                        self.before_slider_command = self.slider_command
                    
                    if data_to_send:
                        self.send_udp_data(data_to_send)

                except socket.error as e:
                    self.show_snackbar(f"UDP Send Error (slider): {e}")
                except Exception as e:
                    print(f"Error sending target value (slider): {e}")

    def update_drawer_menu(self):
        actuator_display_name = self.actuater_name.get(str(self.add_cylinder_num), "Other" + str(self.add_cylinder_num - len(self.actuater_name) if self.add_cylinder_num >= len(self.actuater_name) else self.add_cylinder_num))
        actuater_list_item = MDNavigationDrawerItem(MDNavigationDrawerItemText(text=actuator_display_name))
        actuater_list_item.bind(on_release=partial(self.switch_actuater, str(self.add_cylinder_num)))
        if self.navigation_drawer:
            self.navigation_drawer.add_widget(actuater_list_item)
        self.add_cylinder_num += 1
            
    def show_snackbar(self, message_text):
        snackbar = MDSnackbar(
            MDSnackbarText(text=message_text,),
            pos_hint={"center_x": 0.5, "center_y":0.1},
            size_hint_x=0.5,
        )
        snackbar.open()
    
    def switch_theme_style(self):
        if self.theme_cls.theme_style == "Light":
            self.theme_cls.theme_style = "Dark"
            if hasattr(self, 'ax'): 
                self.ax.spines['top'].set_color('white')
                self.ax.spines['bottom'].set_color('white')
                self.ax.spines['left'].set_color('white')
                self.ax.spines['right'].set_color('white')
                self.ax.tick_params(axis='y', colors='white')
                self.ax.tick_params(axis='x', colors='white')
        else:
            self.theme_cls.theme_style = "Light"
            if hasattr(self, 'ax'):
                self.ax.spines['top'].set_color('black')
                self.ax.spines['bottom'].set_color('black')
                self.ax.spines['left'].set_color('black')
                self.ax.spines['right'].set_color('black')
                self.ax.tick_params(axis='y', colors='black')
                self.ax.tick_params(axis='x', colors='black')
        
        if hasattr(self, 'fig') and hasattr(self.fig, 'canvas') and self.fig.canvas:
            self.fig.canvas.draw_idle()
        self.settings_manager.update_setting('theme', self.theme_cls.theme_style)
        self.settings_manager.save_settings()
    
    # --- CSV Playback Methods ---
    def open_csv_file_chooser_dialog(self):
        if not self._file_path_input_dialog:
            self.file_path_input_field = MDTextField(
                hint_text="Enter full path to CSV file",
                text=self.csv_file_path if self.csv_file_path else os.getcwd(), 
                mode="outlined" 
            )
            content_widget = MDBoxLayout(orientation='vertical', spacing="12dp", size_hint_y=None, padding="10dp")
            content_widget.bind(minimum_height=content_widget.setter('height'))
            content_widget.add_widget(self.file_path_input_field)
            cancel_button = MDButton(MDButtonText(text="CANCEL"), style="text", on_release=lambda x: self._file_path_input_dialog.dismiss())
            load_button = MDButton(MDButtonText(text="LOAD"), style="text", on_release=self._process_csv_path_from_dialog)
            button_container = MDDialogButtonContainer(Widget(), cancel_button, load_button, spacing="8dp")
            self._file_path_input_dialog = MDDialog(MDDialogHeadlineText(text="Load CSV File"), MDDialogContentContainer(content_widget), button_container)
        else: 
            self.file_path_input_field.text = self.csv_file_path if self.csv_file_path else os.getcwd()
        self._file_path_input_dialog.open()

    def _process_csv_path_from_dialog(self, *args):
        path_str = self.file_path_input_field.text.strip() 
        if os.path.exists(path_str) and path_str.lower().endswith(".csv"):
            self._load_csv_data(path_str)
            if self._file_path_input_dialog: 
                self._file_path_input_dialog.dismiss()
        else:
            self.show_snackbar(f"Invalid file: '{os.path.basename(path_str)}'. Must be an existing .csv file.")

    def _load_csv_data(self, file_path_arg): 
        if not file_path_arg:
            self.show_snackbar("No file path provided.")
            self._reset_csv_state() 
            return
        try:
            temp_data_list = [] 
            with open(file_path_arg, 'r', newline='', encoding='utf-8-sig') as csvfile_obj: 
                reader_obj = csv.reader(csvfile_obj) 
                header_row = next(reader_obj, None) 
                if header_row is None:
                    self.show_snackbar(f"CSV file is empty: {os.path.basename(file_path_arg)}")
                    self._reset_csv_state()
                    return

                # expected_columns = 8 # 列数のチェックはmain2.py側に任せるか、ここで緩くする
                
                for i_row, row_list in enumerate(reader_obj): 
                    # if len(row_list) != expected_columns:
                    #     self.show_snackbar(f"Row {i_row+2} has {len(row_list)} columns, expected {expected_columns}.")
                    #     self._reset_csv_state()
                    #     if hasattr(self, 'loaded_csv_filename_label'): self.loaded_csv_filename_label.text = "Load failed"
                    #     return 
                    
                    processed_row = []
                    for value_str in row_list:
                        # main2.py で int() 変換するので、ここでは文字列のまま保持
                        processed_row.append(value_str)
                    temp_data_list.append(processed_row)
            
            self.csv_data = temp_data_list # [[ストリング, ストリング,...], [ストリング,...]]
            self.csv_file_path = file_path_arg 
            self.loaded_csv_filename = os.path.basename(file_path_arg) 
            if hasattr(self, 'loaded_csv_filename_label'): self.loaded_csv_filename_label.text = self.loaded_csv_filename
            self.settings_manager.update_setting('last_csv_path', self.csv_file_path)
            
            self.play_stop_csv_button.disabled = not bool(self.csv_data) 
            if self.csv_data:
                self.show_snackbar(f"Loaded {len(self.csv_data)} rows from {self.loaded_csv_filename}")
            else: 
                self.show_snackbar(f"No data rows found in {self.loaded_csv_filename}")
            # self.current_csv_row_index = 0 # 不要
        except FileNotFoundError:
            self.show_snackbar(f"CSV file not found: {file_path_arg}")
            self._reset_csv_state()
        except Exception as e_csv: 
            self.show_snackbar(f"Error loading CSV: {e_csv}")
            self._reset_csv_state()

    def _reset_csv_state(self):
        self.csv_data = []
        self.csv_file_path = ""
        self.loaded_csv_filename = "No CSV loaded"
        if hasattr(self, 'loaded_csv_filename_label'): self.loaded_csv_filename_label.text = self.loaded_csv_filename
        if hasattr(self, 'play_stop_csv_button'): 
            self.play_stop_csv_button.disabled = True
            self.play_stop_csv_button.icon = "play-circle-outline"
        self.is_csv_playing = False
        # スライダーの状態はCSV再生とは独立して管理されるべきだが、念のため
        if hasattr(self, 'position_slider') and self.root.current == 'main': # main画面表示中のみ
             self.position_slider.disabled = False if self.selected_actuater != 0 else True # selected_actuaterが0(未選択)なら無効
        if hasattr(self, 'command_slider') and self.root.current == 'main':
             self.command_slider.disabled = False if self.selected_actuater != 0 else True


    def toggle_csv_playback(self):
        if not self.csv_data:
            self.show_snackbar("No CSV data loaded. Please load a CSV file first.")
            return

        if not hasattr(self, 'dynamicUdpSocket') or not self.dynamicUdpSocket:
            self.show_snackbar("Not connected to UDP server. Cannot play CSV.")
            return

        self.is_csv_playing = not self.is_csv_playing

        if self.is_csv_playing:
            # --- 再生開始 ---
            self.play_stop_csv_button.icon = "stop-circle-outline"
            self.position_slider.disabled = True
            self.command_slider.disabled = True
            csv_stop_event.clear() # 停止イベントをクリア (UI用)

            playback_interval = 1.0 / 30.0 # 再生間隔 (秒) main2.py に渡す
            
            data_to_send = {
                "type": "set_target_values_bulk",
                "data": self.csv_data, # CSVデータ全体
                "interval": playback_interval,
                "loop": self.loop_csv # ループ再生フラグも送信 (main2.py側での対応が必要)
            }
            self.send_udp_data(data_to_send)
            self.show_snackbar("CSV playback request sent to backend.")

        else:
            # --- 再生停止 ---
            self.send_stop_csv_playback_request()
            # UI更新はバックエンドからのACK受信時に行うのがより確実だが、即時性のためここでも一部行う
            self._update_csv_playback_ui_on_stop(finished=False) 
            self.show_snackbar("CSV stop request sent to backend.")

    def send_stop_csv_playback_request(self):
        """ バックエンドにCSV再生停止リクエストを送信 """
        if hasattr(self, 'dynamicUdpSocket') and self.dynamicUdpSocket:
            stop_data = {"type": "stop_csv_playback"}
            self.send_udp_data(stop_data)
            csv_stop_event.set() # UI制御用イベントもセット
        else:
            print("Cannot send stop CSV request, not connected.")


    def _update_csv_playback_ui_on_stop(self, finished=False):
        """ CSV再生が停止した際のUI更新 """
        self.is_csv_playing = False 
        if hasattr(self, 'play_stop_csv_button'): 
            self.play_stop_csv_button.icon = "play-circle-outline" 
        
        # スライダーの有効化は、アクチュエータが選択されている場合のみ
        is_actuator_selected = self.selected_actuater != 0 # 0は未選択を示すと仮定
        if hasattr(self, 'position_slider'):
            self.position_slider.disabled = not is_actuator_selected
        if hasattr(self, 'command_slider'):
            self.command_slider.disabled = not is_actuator_selected

        if finished: # バックエンドから正常終了通知があった場合
            self.show_snackbar("CSV playback finished.")
        # エラーや途中停止の場合は、それぞれのハンドラでSnackbar表示

        if hasattr(self, 'play_stop_csv_button'): # CSVデータがなければ再生ボタンは無効
            self.play_stop_csv_button.disabled = not bool(self.csv_data)


    def on_loop_csv_changed(self, active_status_bool): 
        self.loop_csv = active_status_bool
        self.settings_manager.update_setting('csv_loop_enabled', self.loop_csv)
        self.show_snackbar(f"Loop CSV: {'Enabled' if self.loop_csv else 'Disabled'}")
        # 注意: ループ再生の実態は main2.py 側で実装される必要がある。
        # 現在の main2.py はループに対応していないため、この設定は main2.py 側で無視される。
    
class SettingsManager: 
    path = os.path.join(rootdir, 'settings.json')
    def __init__(self, filename=path): 
        self.filename = filename
        self.settings = self.load_settings()

    def load_settings(self):
        try:
            with open(self.filename, 'r', encoding='utf-8') as file_obj:
                settings_dict = json.load(file_obj) 
        except FileNotFoundError: 
            print("Warning: Settings file not found. Using default settings.") 
            settings_dict = { 
                "address": "localhost", "theme": "Light", "color": "Green",
                "actuater_name": { 
                    "0": "front right hip", "1": "front right knee", "2": "front left hip",
                    "3": "front left knee", "4": "rear right hip", "5": "rear right knee",
                    "6": "rear left hip", "7": "rear left knee"
                },
                "csv_loop_enabled": False, "last_csv_path": "", "selected_actuater_default": "0"      
            }
        except json.JSONDecodeError:
            print("Warning: Settings file is corrupted. Using default settings.")
            settings_dict = {
                "address": "localhost", "theme": "Light", "color": "Green",
                "actuater_name": {"0": "frh", "1": "frk"},
                "csv_loop_enabled": False, "last_csv_path": "", "selected_actuater_default": "0"
            }
        return settings_dict

    def save_settings(self):
        try:
            with open(self.filename, 'w', encoding='utf-8') as file_obj:
                json.dump(self.settings, file_obj, indent=4, ensure_ascii=False)
        except Exception as e_save: 
            print(f"Error saving settings: {e_save}")

    def update_setting(self, key_str, value_any):
        self.settings[key_str] = value_any

    def get_setting(self, key_str, default=None):
        return self.settings.get(key_str, default)
    
class NonInteractiveCard(MDCard):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.padding = [10, 10, 10, 10]  
    def set_properties_widget(self): 
        return False
    
class CustomMDButton(MDButton):
    def on_touch_down(self, touch): 
        if self.disabled:
            return False
        return super().on_touch_down(touch)

if __name__ == '__main__':
    NativeGUIApp().run()
