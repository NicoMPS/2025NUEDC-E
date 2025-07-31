import gc
import time
import math
import struct
from machine import UART, FPIOA, Pin
from media.sensor import *
from media.display import *
import media

# ======================================================
# 系统初始化
# ======================================================
gc.enable()

# 串口3初始化（BANK4_GPIO50/51）
fpioa = FPIOA()
fpioa.set_function(50, fpioa.UART3_TXD)  # GPIO50作为TX
fpioa.set_function(51, fpioa.UART3_RXD)  # GPIO51作为RX
motor_uart = UART(UART.UART3, 115200, 8, 1, 0, timeout=10)

# 电机参数
MOTOR_ID = 0x01          # 电机固定ID
MAX_SPEED = 1000         # 最大速度（脉冲/秒）
ACCELERATION = 5000      # 加速度（脉冲/秒²）
STEPS_PER_DEGREE = 100   # 每度对应的脉冲数（根据实际调整）

# 视频资源初始化
WIDTH = 800
HEIGHT = 480
FPS = 30
sensor = Sensor(id=2)
sensor.reset()
sensor.set_framesize(width=WIDTH, height=HEIGHT, chn=CAM_CHN_ID_0)
sensor.set_pixformat(Sensor.RGB565, chn=CAM_CHN_ID_0)

Display.init(type=Display.ST7701, width=WIDTH, height=HEIGHT, osd_num=2,
             to_ide=False, fps=FPS, quality=90)
MediaManager.init()
sensor.run()

# ======================================================
# 电机控制协议（自定义简化版）
# ======================================================
def send_motor_command(yaw_angle, pitch_angle):
    """通过串口发送二维电机控制指令"""
    # 角度转脉冲数
    yaw_pulses = int(yaw_angle * STEPS_PER_DEGREE)
    pitch_pulses = int(pitch_angle * STEPS_PER_DEGREE)

    # 构造指令帧（AA 55 [ID] [YAW_PULSES] [PITCH_PULSES] [CHECKSUM]）
    cmd = bytearray()
    cmd.extend(b'\xAA\x55')                     # 帧头
    cmd.append(MOTOR_ID)                        # 电机ID
    cmd.extend(struct.pack('>ii', yaw_pulses, pitch_pulses))  # 两个轴的目标位置
    cmd.append(sum(cmd[2:]) & 0xFF)             # 校验和

    motor_uart.write(cmd)
    time.sleep_ms(5)  # 指令间隔

# ======================================================
# PID控制器（保持原有逻辑）
# ======================================================
BASE_KP = 0.005
BASE_KI = 0.0005
BASE_KD = 0.0001
MAX_INTEGRAL = 20000
MAX_ANGLE = 40

last_error_x = 0
last_error_y = 0
integral_x = 0
integral_y = 0
last_time = time.ticks_ms()

def pid_controller(target_x, target_y, current_x, current_y):
    """优化后的高速PID控制器"""
    global last_error_x, last_error_y, integral_x, integral_y, last_time

    current_time = time.ticks_ms()
    dt = time.ticks_diff(current_time, last_time) / 1000.0
    dt = max(dt, 0.001)
    last_time = current_time

    error_x = target_x - current_x
    error_y = target_y - current_y

    abs_error = max(abs(error_x), abs(error_y))
    if abs_error > 100:
        KP = BASE_KP * 2.0
        KI = BASE_KI * 0.5
        KD = BASE_KD * 0.8
    elif abs_error > 30:
        KP = BASE_KP * 1.5
        KI = BASE_KI * 1.0
        KD = BASE_KD * 1.0
    else:
        KP = BASE_KP * 0.8
        KI = BASE_KI * 1.5
        KD = BASE_KD * 1.2

    P_x = KP * error_x
    P_y = KP * error_y

    integral_x += error_x * dt
    integral_y += error_y * dt
    integral_x = max(min(integral_x, MAX_INTEGRAL), -MAX_INTEGRAL)
    integral_y = max(min(integral_y, MAX_INTEGRAL), -MAX_INTEGRAL)
    I_x = KI * integral_x
    I_y = KI * integral_y

    D_x = KD * (error_x - last_error_x) / dt
    D_y = KD * (error_y - last_error_y) / dt

    last_error_x = error_x
    last_error_y = error_y

    output_x = P_x + I_x + D_x
    output_y = P_y + I_y + D_y

    angle_yaw = output_x * (MAX_ANGLE / (WIDTH//2))
    angle_pitch = output_y * (MAX_ANGLE / (HEIGHT//2))

    return angle_yaw, angle_pitch, error_x, error_y

# ======================================================
# 视觉处理函数（完全保持原样）
# ======================================================
last_laser_point = None
def get_red_blobs(img):
    global last_laser_point
    thresholds = [(27, 100, 39, 127, -51, 127)]
    blobs = img.find_blobs(thresholds, merge=True)
    if not blobs:
        return last_laser_point
    largest_blob = max(blobs, key=lambda b: b.area())
    if largest_blob:
        new_point = (largest_blob.cx(), largest_blob.cy())
        last_laser_point = new_point if not last_laser_point else (
            int(last_laser_point[0]*0.3 + new_point[0]*0.7),
            int(last_laser_point[1]*0.3 + new_point[1]*0.7)
        )
    return last_laser_point

last_rect_point = None
last_corners = None
def get_black_rect(img):
    global last_rect_point, last_corners
    gray_img = img.to_grayscale()
    gray_img.histeq()
    binary_img = gray_img.binary([(55, 255)], invert=False)
    binary_img.erode(2)
    rects = binary_img.find_rects(threshold=8000)
    if not rects:
        return binary_img, None, None
    largest_rect = max(rects, key=lambda r: r[2]*r[3])
    if largest_rect.magnitude() < 100000:
        return binary_img, None, None
    x, y, w, h = largest_rect[0:4]
    corners = largest_rect.corners()
    last_rect_point = (x, y, w, h)
    last_corners = corners
    return binary_img, last_rect_point, last_corners

# ======================================================
# 主循环（仅修改控制部分）
# ======================================================
def main():
    try:
        while True:
            img = sensor.snapshot()
            if img is None:
                time.sleep_ms(10)
                continue

            laser_pos = get_red_blobs(img)
            rect_img, rect_data, corners = get_black_rect(img)

            if corners:
                target_x = sum(c[0] for c in corners) // 4
                target_y = sum(c[1] for c in corners) // 4

            if laser_pos:
                current_x, current_y = laser_pos

            if laser_pos:
                img.draw_cross(current_x, current_y, color=(0, 255, 0), size=10)
                img.draw_string(current_x+10, current_y+10, "Laser",
                              scale=2, color=(0, 255, 0))

            if corners:
                img.draw_rectangle(rect_data[0], rect_data[1], rect_data[2], rect_data[3],
                                 color=(255, 0, 0), thickness=5)
                img.draw_cross(target_x, target_y, color=(0, 0, 255), size=20)

                if laser_pos:
                    distance = math.sqrt((target_x-current_x)**2 + (target_y-current_y)**2)
                    img.draw_line(current_x, current_y, target_x, target_y,
                                color=(255, 255, 255), thickness=2)
                    img.draw_string(WIDTH//2, 20, f"Distance: {distance:.1f}px",
                                  scale=2, color=(255, 255, 0))

            if laser_pos and corners:
                angle_yaw, angle_pitch, x_error, y_error = pid_controller(
                    target_x, target_y, current_x, current_y)

                # 通过串口控制二维电机（Yaw轴反向）
                send_motor_command(-angle_yaw, angle_pitch)

                img.draw_string(10, 360,
                              f"PID Output: Yaw={angle_yaw:.2f}°, Pitch={angle_pitch:.2f}°",
                              scale=2, color=(0, 255, 255))
                img.draw_string(10, 390,
                              f"Error: X={x_error:.1f}, Y={y_error:.1f}",
                              scale=2, color=(0, 255, 255))

            Display.show_image(img, layer=Display.LAYER_OSD0)
            if rect_img:
                img2 = rect_img.mean_pool(4, 4)
                Display.show_image(img2, layer=Display.LAYER_OSD1)

            time.sleep_ms(10)

    except KeyboardInterrupt:
        print("程序被用户中断")
    except Exception as e:
        print(f"主循环错误: {str(e)[:100]}")
    finally:
        # 发送停止指令
        motor_uart.write(b'\xAA\x55\x01\x00\x00\x00\x00\x56')  # 停止指令示例
        sensor.stop()
        Display.deinit()
        gc.collect()
        print("系统安全关闭")

if __name__ == "__main__":
    main()




#双轴电机控制
#import gc
#import time
#import math
#import struct
#from machine import UART, FPIOA, Pin
#from media.sensor import *
#from media.display import *
#import media

## ======================================================
## 系统初始化
## ======================================================
#gc.enable()

## 硬件配置（根据K230手册）
## UART2: GPIO11(TX)/GPIO12(RX) - 控制Yaw电机
## UART3: GPIO50(TX)/GPIO51(RX) - 控制Pitch电机
#fpioa = FPIOA()

## 初始化UART2 (Yaw轴电机)
#fpioa.set_function(11, fpioa.UART2_TXD)  # GPIO11作为UART2_TX
#fpioa.set_function(12, fpioa.UART2_RXD)  # GPIO12作为UART2_RX（可选）
#yaw_uart = UART(UART.UART2, 115200, 8, 1, 0, timeout=10)

## 初始化UART3 (Pitch轴电机)
#fpioa.set_function(50, fpioa.UART3_TXD)  # GPIO50作为UART3_TX
#fpioa.set_function(51, fpioa.UART3_RXD)  # GPIO51作为UART3_RX（可选）
#pitch_uart = UART(UART.UART3, 115200, 8, 1, 0, timeout=10)

## 电机参数
#YAW_ID = 0x01            # Yaw电机ID
#PITCH_ID = 0x02          # Pitch电机ID
#STEPS_PER_DEGREE = 100   # 每度脉冲数（需校准）
#YAW_REVERSE = True       # Yaw轴方向是否反向

## 视频资源初始化
#WIDTH, HEIGHT = 800, 480
#FPS = 30
#sensor = Sensor(id=2)
#sensor.reset()
#sensor.set_framesize(width=WIDTH, height=HEIGHT, chn=CAM_CHN_ID_0)
#sensor.set_pixformat(Sensor.RGB565, chn=CAM_CHN_ID_0)

#Display.init(type=Display.ST7701, width=WIDTH, height=HEIGHT, osd_num=2,
#             to_ide=False, fps=FPS, quality=90)
#MediaManager.init()
#sensor.run()

## ======================================================
## 独立电机控制协议
## ======================================================
#def send_yaw_command(angle):
#    """控制Yaw轴电机"""
#    pulses = int(angle * STEPS_PER_DEGREE * (-1 if YAW_REVERSE else 1))
#    cmd = bytearray()
#    cmd.extend(b'\xAA\x55')             # 帧头
#    cmd.append(YAW_ID)                  # 电机ID
#    cmd.extend(struct.pack('>i', pulses))  # 32位有符号位置
#    cmd.append(sum(cmd[2:]) & 0xFF)     # 校验和
#    yaw_uart.write(cmd)

#def send_pitch_command(angle):
#    """控制Pitch轴电机"""
#    pulses = int(angle * STEPS_PER_DEGREE)
#    cmd = bytearray()
#    cmd.extend(b'\xAA\x55')             # 帧头
#    cmd.append(PITCH_ID)                # 电机ID
#    cmd.extend(struct.pack('>i', pulses))
#    cmd.append(sum(cmd[2:]) & 0xFF)
#    pitch_uart.write(cmd)

#def control_motors(yaw_angle, pitch_angle):
#    """同步控制双电机"""
#    send_yaw_command(yaw_angle)
#    send_pitch_command(pitch_angle)
#    time.sleep_ms(5)  # 指令间隔

## ======================================================
## PID控制器（双轴独立计算）
## ======================================================
#BASE_KP = 0.008
#BASE_KI = 0.0002
#BASE_KD = 0.0005
#MAX_INTEGRAL = 20000
#MAX_ANGLE = 45  # 单轴最大偏转角度

#last_error_x = last_error_y = 0
#integral_x = integral_y = 0
#last_time = time.ticks_ms()

#def pid_controller(target_x, target_y, current_x, current_y):
#    """双轴PID控制器"""
#    global last_error_x, last_error_y, integral_x, integral_y, last_time

#    # 计算时间差
#    current_time = time.ticks_ms()
#    dt = time.ticks_diff(current_time, last_time) / 1000.0
#    dt = max(dt, 0.001)
#    last_time = current_time

#    # 当前误差
#    error_x = target_x - current_x
#    error_y = target_y - current_y

#    # 动态PID参数
#    abs_error = math.sqrt(error_x**2 + error_y**2)
#    if abs_error > 100:
#        KP = BASE_KP * 0.8
#        KI = BASE_KI * 0.5
#        KD = BASE_KD * 1.2
#    elif abs_error > 30:
#        KP = BASE_KP * 1.2
#        KI = BASE_KI * 0.8
#        KD = BASE_KD * 1.0
#    else:
#        KP = BASE_KP * 1.5
#        KI = BASE_KI * 1.5
#        KD = BASE_KD * 0.8

#    # PID计算
#    P_x = KP * error_x
#    P_y = KP * error_y

#    integral_x += error_x * dt
#    integral_y += error_y * dt
#    integral_x = max(min(integral_x, MAX_INTEGRAL), -MAX_INTEGRAL)
#    integral_y = max(min(integral_y, MAX_INTEGRAL), -MAX_INTEGRAL)
#    I_x = KI * integral_x
#    I_y = KI * integral_y

#    D_x = KD * (error_x - last_error_x) / dt
#    D_y = KD * (error_y - last_error_y) / dt

#    last_error_x = error_x
#    last_error_y = error_y

#    # 转换为角度
#    angle_yaw = (P_x + I_x + D_x) * (MAX_ANGLE / (WIDTH//2))
#    angle_pitch = (P_y + I_y + D_y) * (MAX_ANGLE / (HEIGHT//2))

#    return angle_yaw, angle_pitch, error_x, error_y

## ======================================================
## 视觉处理（保持原样）
## ======================================================
#last_laser_point = None
#def get_red_blobs(img):
#    """检测红色激光点"""
#    global last_laser_point
#    thresholds = [(27, 100, 39, 127, -51, 127)]
#    blobs = img.find_blobs(thresholds, merge=True)
#    if not blobs:
#        return last_laser_point

#    largest = max(blobs, key=lambda b: b.area())
#    new_point = (largest.cx(), largest.cy())
#    if last_laser_point:
#        last_laser_point = (
#            int(last_laser_point[0]*0.3 + new_point[0]*0.7),
#            int(last_laser_point[1]*0.3 + new_point[1]*0.7)
#        )
#    else:
#        last_laser_point = new_point
#    return last_laser_point

#last_corners = None
#def get_black_rect(img):
#    """检测黑色矩形框"""
#    global last_corners
#    gray = img.to_grayscale().histeq()
#    binary = gray.binary([(55, 255)], invert=False).erode(2)
#    rects = binary.find_rects(threshold=8000)
#    if not rects:
#        return None

#    largest = max(rects, key=lambda r: r.magnitude())
#    if largest.magnitude() < 100000:
#        return None

#    last_corners = largest.corners()
#    return last_corners

## ======================================================
## 主控制循环
## ======================================================
#def main():
#    try:
#        # 电机初始化测试
#        print("Motor calibration...")
#        control_motors(30, 15)  # Yaw右转30°，Pitch上仰15°
#        time.sleep(2)
#        control_motors(0, 0)    # 归零
#        time.sleep(1)

#        while True:
#            img = sensor.snapshot()
#            if not img:
#                time.sleep_ms(10)
#                continue

#            # 目标检测
#            laser_pos = get_red_blobs(img)
#            corners = get_black_rect(img)

#            # 计算目标中心
#            target_x = target_y = None
#            if corners:
#                target_x = sum(c[0] for c in corners) // 4
#                target_y = sum(c[1] for c in corners) // 4

#            # 当前位置
#            current_x = current_y = None
#            if laser_pos:
#                current_x, current_y = laser_pos

#            # 视觉反馈绘制
#            if laser_pos:
#                img.draw_cross(current_x, current_y, color=(0, 255, 0), size=10)
#                img.draw_string(current_x+10, current_y+10, "Laser",
#                              scale=2, color=(0, 255, 0))

#            if corners:
#                img.draw_rectangle(corners[0][0], corners[0][1],
#                                 corners[2][0]-corners[0][0],
#                                 corners[2][1]-corners[0][1],
#                                 color=(255, 0, 0), thickness=5)
#                img.draw_cross(target_x, target_y, color=(0, 0, 255), size=20)

#                if laser_pos:
#                    distance = math.sqrt((target_x-current_x)**2 + (target_y-current_y)**2)
#                    img.draw_line(current_x, current_y, target_x, target_y,
#                                color=(255, 255, 255), thickness=2)
#                    img.draw_string(WIDTH//2, 20, f"Dist: {distance:.1f}px",
#                                  scale=2, color=(255, 255, 0))

#            # PID控制
#            if all([laser_pos, corners, target_x, target_y, current_x, current_y]):
#                angle_yaw, angle_pitch, err_x, err_y = pid_controller(
#                    target_x, target_y, current_x, current_y)

#                # 驱动双电机
#                control_motors(angle_yaw, angle_pitch)

#                # 显示控制信息
#                img.draw_string(10, 360,
#                              f"Yaw: {angle_yaw:.1f}° Pitch: {angle_pitch:.1f}°",
#                              scale=2, color=(0, 255, 255))
#                img.draw_string(10, 390,
#                              f"Error: X={err_x:.1f} Y={err_y:.1f}",
#                              scale=2, color=(0, 255, 255))

#            Display.show_image(img, layer=Display.LAYER_OSD0)
#            time.sleep_ms(10)

#    except KeyboardInterrupt:
#        print("User stopped")
#    except Exception as e:
#        print(f"Error: {e}")
#    finally:
#        control_motors(0, 0)  # 电机归零
#        sensor.stop()
#        Display.deinit()
#        gc.collect()

#if __name__ == "__main__":
#    main()
