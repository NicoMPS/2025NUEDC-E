import time, os, gc, sys
from media.sensor import *
from media.display import *
from media.media import *
from machine import TOUCH

# ================ 系统配置 ================
DISPLAY_WIDTH = 800
DISPLAY_HEIGHT = 480
DETECT_WIDTH = ALIGN_UP(600, 16)
DETECT_HEIGHT = 480

# ================ 阈值参数配置 ================
THRESHOLD_CONFIG = {
    "BLACK_GRAY_THRESHOLD": {
        "name": "边框黑度",
        "min_val": 0,
        "max_val": 255,
        "default": 100,
        "color": (255, 0, 0)  # 红色
    },
    "CENTER_GRAY_THRESHOLD": {
        "name": "中心亮度",
        "min_val": 80,
        "max_val": 150,
        "default": 110,
        "color": (0, 255, 0)  # 绿色
    },
    "RECT_DETECT_THRESHOLD": {
        "name": "检测灵敏度",
        "min_val": 1000,
        "max_val": 3000,
        "default": 2000,
        "color": (0, 0, 255)  # 蓝色
    }
}

# ================ 功能按钮 ================
FUNCTION_BUTTONS = {
    "重置": {"rect": (620, 100, 150, 60), "color": (255, 165, 0)},  # 橙色
    "保存": {"rect": (620, 180, 150, 60), "color": (0, 200, 0)},    # 绿色
    "退出": {"rect": (620, 260, 150, 60), "color": (200, 50, 50)}   # 红色
}

# ================ 全局变量 ================
sensor = None
tp = None
current_values = {key: cfg["default"] for key, cfg in THRESHOLD_CONFIG.items()}
adjust_mode = True  # 默认进入调整模式

def camera_init():
    global sensor, tp
    try:
        # 初始化摄像头
        sensor = Sensor(width=DETECT_WIDTH, height=DETECT_HEIGHT)
        sensor.reset()
        sensor.set_framesize(width=DETECT_WIDTH, height=DETECT_HEIGHT)
        sensor.set_pixformat(Sensor.RGB565)

        # 初始化显示
        Display.init(Display.ST7701, width=DISPLAY_WIDTH, height=DISPLAY_HEIGHT, fps=15)
        MediaManager.init()

        # 初始化触摸屏
        tp = TOUCH(0)
        sensor.run()
        print("Camera initialized")
    except Exception as e:
        print(f"Camera init failed: {e}")
        raise

def camera_deinit():
    global sensor, tp
    try:
        if sensor: sensor.stop()
        if tp: tp.deinit()
        Display.deinit()
        MediaManager.deinit()
    except Exception as e:
        print(f"Camera deinit error: {e}")

def draw_threshold_sliders(img):
    """绘制阈值调节滑块"""
    # 绘制标题
    img.draw_string(20, 20, "阈值调节面板", color=(255, 255, 0), scale=3)

    # 绘制滑块区域背景
    img.draw_rectangle(50, 50, 500, 400, color=(30, 30, 30), fill=True, alpha=150)

    # 绘制每个阈值滑块
    for i, (key, cfg) in enumerate(THRESHOLD_CONFIG.items()):
        y_pos = 100 + i * 120
        track_x1, track_x2 = 100, 500

        # 计算滑块位置
        ratio = (current_values[key] - cfg["min_val"]) / (cfg["max_val"] - cfg["min_val"])
        thumb_x = track_x1 + int(ratio * (track_x2 - track_x1))

        # 绘制参数名称
        img.draw_string(track_x1, y_pos - 30,
                       f"{cfg['name']}: {current_values[key]}",
                       color=(255, 255, 255), scale=2.5)

        # 绘制滑轨
        img.draw_line(track_x1, y_pos, track_x2, y_pos,
                     color=(200, 200, 200), thickness=10)

        # 绘制滑块
        img.draw_circle(thumb_x, y_pos, 25, color=cfg["color"], fill=True)

        # 绘制最小值/最大值标签
        img.draw_string(track_x1 - 50, y_pos + 15, str(cfg["min_val"]),
                       color=(200, 200, 200), scale=1.5)
        img.draw_string(track_x2 + 20, y_pos + 15, str(cfg["max_val"]),
                       color=(200, 200, 200), scale=1.5)

def draw_function_buttons(img):
    """绘制功能按钮"""
    for name, btn in FUNCTION_BUTTONS.items():
        # 绘制按钮背景
        img.draw_rectangle(btn["rect"][0], btn["rect"][1],
                          btn["rect"][2], btn["rect"][3],
                          color=btn["color"], fill=True)

        # 绘制按钮文字
        text_x = btn["rect"][0] + (btn["rect"][2] - len(name)*20) // 2
        img.draw_string(text_x, btn["rect"][1] + 15,
                       name, color=(255, 255, 255), scale=2.5)

def handle_touch():
    """处理触摸事件"""
    p = tp.read(1)
    if p == (): return False

    x, y = p[0].x, p[0].y
    print(f"Touch at ({x}, {y})")  # 调试触摸位置

    # 检查滑块触摸
    for i, (key, cfg) in enumerate(THRESHOLD_CONFIG.items()):
        y_pos = 100 + i * 120
        if 100 <= x <= 500 and y_pos - 30 <= y <= y_pos + 30:
            # 计算新值
            new_val = int(cfg["min_val"] + (x - 100) / 400 * (cfg["max_val"] - cfg["min_val"]))
            current_values[key] = max(cfg["min_val"], min(cfg["max_val"], new_val))
            print(f"{cfg['name']} updated to {current_values[key]}")
            return True

    # 检查功能按钮触摸
    for name, btn in FUNCTION_BUTTONS.items():
        rect = btn["rect"]
        if rect[0] <= x <= rect[0] + rect[2] and rect[1] <= y <= rect[1] + rect[3]:
            if name == "重置":
                for key in current_values:
                    current_values[key] = THRESHOLD_CONFIG[key]["default"]
                print("重置所有阈值")
            elif name == "保存":
                print("保存当前阈值设置")
            elif name == "退出":
                global adjust_mode
                adjust_mode = False
                print("退出调整模式")
            return True

    # 检查返回调整按钮（仅在非调整模式下）
    if not adjust_mode and 620 <= x <= 770 and 400 <= y <= 460:
        global adjust_mode
        adjust_mode = True
        print("返回调整模式")
        return True

    return False

#def detect_outer_rectangle(img):
#    """使用当前阈值检测外接矩形"""
#    # 转换为灰度图像
#    gray = img.to_grayscale()

#    # 查找矩形 (使用当前灵敏度阈值)
#    counts = gray.find_rects(threshold=current_values["RECT_DETECT_THRESHOLD"])

#    # 筛选最佳矩形
#    best_rect = None
#    max_area = 0
#    for r in counts:
#        x, y, w, h = r.rect()
#        area = w * h
#        if area > max_area:
#            max_area = area
#            best_rect = r

#    if best_rect:
#        x1, y1 = best_rect.rect()[0], best_rect.rect()[1]
#        x2, y2 = x1 + best_rect.rect()[2], y1 + best_rect.rect()[3]
#        center_x, center_y = (x1 + x2) // 2, (y1 + y2) // 2
#        screen_center_x, screen_center_y = img.width()//2, img.height()//2

#        # 检查边框和中心是否符合阈值
#        border_roi = (x1, y1, best_rect.rect()[2], 5)
#        border_gray = gray.get_statistics(roi=border_roi).mean()
#        center_gray = gray.get_statistics(roi=(center_x, center_y, 4, 4)).mean()

#        if (border_gray < current_values["BLACK_GRAY_THRESHOLD"] and
#            center_gray > current_values["CENTER_GRAY_THRESHOLD"]):

#            # 绘制检测结果 - 更醒目的可视化
#            # 1. 绘制红色矩形框（加粗）
#            img.draw_rectangle(best_rect.rect(), color=(255, 0, 0), thickness=4)

#            # 2. 绘制矩形中心大红点（直径15像素）
#            img.draw_circle(center_x, center_y, 10, color=(255, 0, 0), fill=True)
#            img.draw_circle(center_x, center_y, 6, color=(255, 255, 255), fill=True)  # 中心白点增强对比

#            # 3. 绘制屏幕中心大绿点（直径15像素）
#            img.draw_circle(screen_center_x, screen_center_y, 10, color=(0, 255, 0), fill=True)
#            img.draw_circle(screen_center_x, screen_center_y, 6, color=(255, 255, 255), fill=True)

#            # 4. 绘制中心连线（加粗绿色线）
#            img.draw_line(center_x, center_y, screen_center_x, screen_center_y,
#                         color=(0, 255, 0), thickness=4)

#            # 5. 添加距离信息
#            dx = center_x - screen_center_x
#            dy = center_y - screen_center_y
#            distance = (dx**2 + dy**2)**0.5
#            img.draw_string(center_x + 20, center_y - 20,
#                          f"ΔX:{dx} ΔY:{dy}",
#                          color=(255, 255, 255), scale=1.5)

#            return True

#    return False

def detect_outer_rectangle(img):
    img_width = img.width()
    img_height = img.height()
    img_centerx = img_width//2
    img_centery = img_height//2
    gray = img.to_grayscale()
    counts = gray.find_rects(threshold=current_values["RECT_DETECT_THRESHOLD"])

    best_rect = None
    max_area = 0
    for r in counts:
        x, y, w, h = r.rect()
        area = w * h
        if area > max_area:
            max_area = area
            best_rect = r

    if best_rect:
        x1, y1 = best_rect.rect()[0], best_rect.rect()[1]
        x2, y2 = x1 + best_rect.rect()[2], y1 + best_rect.rect()[3]
        center_x, center_y = (x1 + x2) // 2, (y1 + y2) // 2

        # 计算边框和中心灰度值
        border_roi = (x1, y1, best_rect.rect()[2], 5)
        border_gray = gray.get_statistics(roi=border_roi).mean()
        center_gray = gray.get_statistics(roi=(center_x, center_y, 4, 4)).mean()

        # 输出格式与第二个程序保持一致
        print(f"Center gray: {center_gray}, Border gray: {border_gray}")

        if (border_gray < current_values["BLACK_GRAY_THRESHOLD"] and
            center_gray > current_values["CENTER_GRAY_THRESHOLD"]):

            # 计算偏移量
            dx = center_x - img_centerx
            dy = center_y - img_centery

            # 输出坐标信息（与第二个程序相同格式）
            print(f"Center: ({center_x}, {center_y}), ScreenCenter: ({img_centerx}, {img_centery})")
            print(f"Offset: ΔX={dx}, ΔY={dy}")

            # 绘制检测结果
            img.draw_rectangle(best_rect.rect(), color=(255,0,0), thickness=2)
            img.draw_circle(center_x, center_y, 5, color=(255,0,0), fill=True)
            img.draw_line(center_x, center_y, img_centerx, img_centery,
                         color=(0,255,0), thickness=2)
            img.draw_circle(img_centerx, img_centery, 5, color=(0,0,255), fill=True)

            return True

    print("No target detected")  # 未检测到目标时也输出提示
    return False

def main_loop():
    fps = time.clock()
    while True:
        fps.tick()
        try:
            os.exitpoint()

            # 获取图像
            img = sensor.snapshot()

            # 处理触摸事件
            handle_touch()

            if adjust_mode:
                # 调整模式：显示阈值调节界面
                draw_threshold_sliders(img)
                draw_function_buttons(img)
            else:
                # 检测模式：执行矩形检测
                if detect_outer_rectangle(img):
                    img.draw_string(20, 20, "检测成功!", color=(0, 255, 0), scale=3)
                else:
                    img.draw_string(20, 20, "未检测到目标", color=(255, 0, 0), scale=3)

                # 显示当前阈值设置
                img.draw_string(20, 60, f"边框阈值: {current_values['BLACK_GRAY_THRESHOLD']}",
                               color=(255, 255, 255), scale=2)
                img.draw_string(20, 100, f"中心阈值: {current_values['CENTER_GRAY_THRESHOLD']}",
                               color=(255, 255, 255), scale=2)
                img.draw_string(20, 140, f"检测灵敏度: {current_values['RECT_DETECT_THRESHOLD']}",
                               color=(255, 255, 255), scale=2)

                # 绘制返回调整按钮（更醒目的设计）
                img.draw_rectangle(620, 400, 150, 60, color=(0, 150, 255), fill=True)
                img.draw_string(635, 415, "返回调整", color=(255, 255, 255), scale=2.5)

            # 显示帧率
            img.draw_string(DISPLAY_WIDTH - 150, DISPLAY_HEIGHT - 40,
                           f"FPS: {fps.fps():.1f}", color=(255, 255, 255), scale=2)

            # 显示图像
            Display.show_image(img)
            gc.collect()

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"Error: {e}")
            break

def main():
    os.exitpoint(os.EXITPOINT_ENABLE)
    try:
        camera_init()
        main_loop()
    except Exception as e:
        print(f"Main error: {e}")
    finally:
        camera_deinit()
        print("Program ended")

if __name__ == "__main__":
    main()
