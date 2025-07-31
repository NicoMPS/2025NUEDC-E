import time, os, gc, sys
import math
from media.sensor import *
from media.display import *
from media.media import *
from machine import UART, FPIOA, TOUCH

# ================ 系统配置 ================
DISPLAY_WIDTH = 800
DISPLAY_HEIGHT = 480
DETECT_WIDTH = ALIGN_UP(480, 16)
DETECT_HEIGHT = 320

# ================ A4纸物理尺寸(mm) ================
A4_WIDTH_MM = 210
A4_HEIGHT_MM = 297

# ================ 摄像头参数 ================
FOCAL_LENGTH_PX = 500
SENSOR_WIDTH_MM = 4.8
SENSOR_HEIGHT_MM = 3.6

# ================ 固定阈值配置 ================
THRESHOLD_VALUES = {
    "BLACK_GRAY_THRESHOLD": 149,  # 边框黑度阈值
    "CENTER_GRAY_THRESHOLD": 128, # 中心亮度阈值
    "RECT_DETECT_THRESHOLD": 2500 # 矩形检测灵敏度
}

# ================ 矩形宽高比限制 ================
MIN_ASPECT_RATIO = 1.1
MAX_ASPECT_RATIO = 1.8

# ================ 串口配置 ================
UART_PORT = 2
UART_BAUDRATE = 115200
UART_TX_PIN = 11
UART_RX_PIN = 12
HEADER = 0x55
CHECKSUM = 0x77
FOOTER = 0x44

# ================ 全局变量 ================
sensor = None
uart = None
tp = None
running = True
img_okcount = 0
last_send_time = 0

def camera_init():
    global sensor, uart, tp
    try:
        print("Initializing camera...")
        sensor = Sensor(width=DETECT_WIDTH, height=DETECT_HEIGHT)
        sensor.reset()
        sensor.set_framesize(width=DETECT_WIDTH, height=DETECT_HEIGHT)
        sensor.set_pixformat(Sensor.RGB565)

        # 初始化串口
        print("Initializing UART2...")
        fpioa = FPIOA()
        fpioa.set_function(UART_TX_PIN, FPIOA.UART2_TXD)
        fpioa.set_function(UART_RX_PIN, FPIOA.UART2_RXD)

        uart = UART(UART_PORT, baudrate=UART_BAUDRATE)
        uart.init(
            baudrate=UART_BAUDRATE,
            bits=UART.EIGHTBITS,
            parity=UART.PARITY_NONE,
            stop=UART.STOPBITS_ONE
        )
        print(f"UART2 initialized at {UART_BAUDRATE} baud")

        # 初始化显示
        Display.init(Display.ST7701, width=DISPLAY_WIDTH, height=DISPLAY_HEIGHT, fps=30)
        MediaManager.init()
        sensor.run()
        print("Camera initialization completed")
    except Exception as e:
        print(f"Camera init failed: {e}")
        raise

def camera_deinit():
    global sensor, uart, tp
    try:
        if sensor: sensor.stop()
        if uart: uart.deinit()
        Display.deinit()
        MediaManager.deinit()
    except Exception as e:
        print(f"Camera deinit error: {e}")

def calculate_physical_position(rect, img_width, img_height):
    """
    计算A4纸上的物理坐标(mm)
    参数:
        rect: 检测到的矩形 (x,y,w,h)
        img_width: 图像宽度(像素)
        img_height: 图像高度(像素)
    返回:
        (distance_mm, center_x_mm, center_y_mm, width_mm, height_mm)
    """
    x, y, w, h = rect
    pixel_width = w
    pixel_height = h

    # 计算实际距离
    distance_mm_width = (A4_WIDTH_MM * FOCAL_LENGTH_PX) / pixel_width
    distance_mm_height = (A4_HEIGHT_MM * FOCAL_LENGTH_PX) / pixel_height
    distance_mm = (distance_mm_width + distance_mm_height) / 2
    distance_mm = max(500, min(1600, distance_mm))

    # 计算物理坐标
    center_x_px = x + w/2 - img_width/2
    center_y_px = y + h/2 - img_height/2
    center_x_mm = (center_x_px * A4_WIDTH_MM) / pixel_width
    center_y_mm = (center_y_px * A4_HEIGHT_MM) / pixel_height

    # 计算实际尺寸
    width_mm = (w * A4_WIDTH_MM) / pixel_width
    height_mm = (h * A4_HEIGHT_MM) / pixel_height

    return distance_mm, center_x_mm, center_y_mm, width_mm, height_mm

def send_uart_data(center_x, center_y, delta_x, delta_y, physical_data):
    """通过串口2发送数据，频率控制在50Hz"""
    global uart, last_send_time

    current_time = time.ticks_ms()
    if current_time - last_send_time < 20:
        return False

    if not uart:
        return False

    distance_mm, center_x_mm, center_y_mm, _, _ = physical_data

    data = bytearray([
        HEADER,
        (center_x >> 8) & 0xFF, center_x & 0xFF,
        (center_y >> 8) & 0xFF, center_y & 0xFF,
        (delta_x >> 8) & 0xFF, delta_x & 0xFF,
        (delta_y >> 8) & 0xFF, delta_y & 0xFF,
        int(distance_mm) >> 8 & 0xFF, int(distance_mm) & 0xFF,
        int(center_x_mm * 10) >> 8 & 0xFF, int(center_x_mm * 10) & 0xFF,
        int(center_y_mm * 10) >> 8 & 0xFF, int(center_y_mm * 10) & 0xFF,
        CHECKSUM,
        FOOTER
    ])

    try:
        uart.write(data)
        last_send_time = current_time
        print(f"[UART] 发送: {[hex(b) for b in data]}")
        print(f"物理坐标: 距离={distance_mm:.1f}mm, X={center_x_mm:.1f}mm, Y={center_y_mm:.1f}mm")
        return True
    except Exception as e:
        print(f"串口发送失败: {e}")
        return False

def detect_outer_rectangle(img):
    """使用固定阈值检测外接矩形"""
    global img_okcount

    try:
        if img is None:
            print("错误: 输入图像为空")
            return False

        img_width = img.width()
        img_height = img.height()
        img_centerx = img_width // 2
        img_centery = img_height // 2

        gray = img.to_grayscale()
        counts = gray.find_rects(threshold=THRESHOLD_VALUES["RECT_DETECT_THRESHOLD"])

        best_rect = None
        max_area = 0
        for r in counts:
            x, y, w, h = r.rect()
            area = w * h
            aspect_ratio = float(w) / h

            if MIN_ASPECT_RATIO < aspect_ratio < MAX_ASPECT_RATIO and area > max_area:
                max_area = area
                best_rect = r

        if best_rect:
            x1, y1 = best_rect.rect()[0], best_rect.rect()[1]
            x2, y2 = x1 + best_rect.rect()[2], y1 + best_rect.rect()[3]
            center_x, center_y = (x1 + x2) // 2, (y1 + y2) // 2
            delta_x = center_x - img_centerx
            delta_y = center_y - img_centery

            border_gray = gray.get_statistics(roi=(x1, y1, best_rect.rect()[2], 5)).mean()
            center_gray = gray.get_statistics(roi=(center_x, center_y, 4, 4)).mean()

            if (border_gray < THRESHOLD_VALUES["BLACK_GRAY_THRESHOLD"] and
                center_gray > THRESHOLD_VALUES["CENTER_GRAY_THRESHOLD"]):
                img_okcount += 1
                physical_data = calculate_physical_position(best_rect.rect(), img_width, img_height)

                # 绘制检测结果
                img.draw_rectangle(best_rect.rect(), color=(255, 0, 0), thickness=2)
                img.draw_circle(center_x, center_y, 5, color=(255, 0, 0), fill=True)
                img.draw_line(center_x, center_y, img_centerx, img_centery,
                            color=(0, 255, 0), thickness=2)
                img.draw_circle(img_centerx, img_centery, 5, color=(0, 0, 255), fill=True)

                img.draw_string(10, 10, f"检测成功: {img_okcount}", color=(255,255,255), scale=2)
                img.draw_string(10, 40, f"宽高比: {float(best_rect.rect()[2])/best_rect.rect()[3]:.2f}",
                            color=(255,255,255), scale=1.5)
                img.draw_string(10, 70, f"距离: {physical_data[0]:.1f}mm", color=(255,255,255), scale=1.5)
                img.draw_string(10, 100, f"物理坐标: X={physical_data[1]:.1f}mm Y={physical_data[2]:.1f}mm",
                            color=(255,255,255), scale=1.2)

                send_uart_data(center_x, center_y, delta_x, delta_y, physical_data)
                return True

        img.draw_string(10, 10, "未检测到目标", color=(255,0,0), scale=2)
        return False
    except Exception as e:
        print(f"检测错误: {e}")
        return False

def main_loop():
    fps = time.clock()
    while running:
        try:
            fps.tick()
            os.exitpoint()

            img = sensor.snapshot()

            if detect_outer_rectangle(img):
                img.draw_string(20, 20, "检测成功!", color=(0, 255, 0), scale=3)
            else:
                img.draw_string(20, 20, "未检测到目标", color=(255, 0, 0), scale=3)

            # 显示固定阈值信息
            img.draw_string(20, 60, f"边框阈值: {THRESHOLD_VALUES['BLACK_GRAY_THRESHOLD']}",
                          color=(255, 255, 255), scale=2)
            img.draw_string(20, 100, f"中心阈值: {THRESHOLD_VALUES['CENTER_GRAY_THRESHOLD']}",
                          color=(255, 255, 255), scale=2)
            img.draw_string(20, 140, f"检测灵敏度: {THRESHOLD_VALUES['RECT_DETECT_THRESHOLD']}",
                          color=(255, 255, 255), scale=2)

            img.draw_string(DISPLAY_WIDTH - 150, DISPLAY_HEIGHT - 40,
                          f"FPS: {fps.fps():.1f}", color=(255, 255, 255), scale=2)
            Display.show_image(img)
            gc.collect()

        except KeyboardInterrupt:
            global running
            running = False
        except Exception as e:
            print(f"主循环错误: {e}")
            time.sleep(0.5)

def main():
    os.exitpoint(os.EXITPOINT_ENABLE)
    try:
        camera_init()
        main_loop()
    except Exception as e:
        print(f"主程序错误: {e}")
    finally:
        camera_deinit()
        print("程序结束")

if __name__ == "__main__":
    main()
