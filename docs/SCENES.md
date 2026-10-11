# 湖景重绘与动效记录

2026-10-11 使用内置 `image_gen__imagegen`，分别以原湖岸树木和湖屋照片为编辑目标，重绘细节并保持构图。每张图尝试了两次高分辨率请求，工具实际返回约 1600 × 980，最终选用构图更稳定的第一版，均为 **1606 × 979**。使用者确认使用当前重绘图；没有进行插值放大，没有使用 API/CLI 生成，也不将结果标称为原生 4K。

最终运行文件：

- `assets/visuals/training-lake-trees.png`
- `assets/visuals/training-lake-house.png`

原照片保存在 Git 历史中。重绘未加入人物、文字、水印或新建筑。

## 湖岸树木最终提示词

```text
Use case: photorealistic-natural
Asset type: quiet landscape background for an EEG focus-training application.
Input image 1 is the edit target and exact composition reference.
Primary request: redraw this low-resolution lakeside landscape as a high-resolution, refined natural photograph with clearly resolved foliage, bark, grasses, subtle ripples, distant terrain and cloud details. Preserve the original scene and framing very faithfully, without copying compression or blur.
Composition/framing: wide landscape, original aspect ratio 64:39 (1920:1170). Render at 3840 by 2340 pixels if supported, otherwise the highest native resolution available at the same aspect ratio. Preserve all major subject positions and boundary heights. Blue-turquoise water occupies the bottom approximately 22% of the image, with a near-horizontal waterline at y=0.775. The narrow green grassy shoreline spans full width between y=0.70 and y=0.775. One large leafy tree is centered near x=0.435, with its crown spanning roughly x=0.35–0.52 and y=0.42–0.66; its trunk reaches the shore. A smaller leafy tree is right of it centered near x=0.57, crown roughly x=0.505–0.63 and y=0.53–0.675. Keep the small sapling just right of the pair near x=0.66. Broad distant green-blue mountains span behind the trees, with a ridge boundary between y=0.27 and y=0.38, high on the right and gently lower in the middle. Blue sky and soft white cumulus clouds fill the upper part, as in the reference.
Style/medium: realistic outdoor landscape photography, calm and restful, natural daylight, gentle blue-green palette, believable exposure and atmospheric perspective, soft yet detailed rather than sharpened.
Constraints: keep the same viewpoint, crop, two main trees, tiny sapling, grass shore, distant mountains and sky; no new buildings, people, animals, boats or objects. No words, logos, borders or watermark. No exaggerated saturation, HDR glow, crunchy sharpening, painted or fantasy look. Change only the resolution and fine detail, with a modest correction toward natural colors.
```

## 湖屋最终提示词

```text
Use case: photorealistic-natural. Asset type: high-resolution peaceful landscape background for an EEG attention training application. Input image 1 is the EDIT TARGET, not an inspiration for a different landscape. Redraw this same photograph at the highest native resolution the tool supports, targeting roughly 3680 x 2240 pixels (original landscape aspect ratio 1.64:1), with genuinely newly rendered fine photographic detail. Preserve the original camera framing, exact arrangement, subject scale, horizon, and quiet mood. A single modest wooden lakeside cabin remains slightly right of center, its gray tile roof about y=0.44 and base about y=0.66, the open glass porch facing the camera, reddish-brown horizontal balcony railing and wood paneling, darker right side. Keep the same low green willow/bush at the cabin's left, the same soft out-of-focus reeds framing the left and right foreground edges, subtle blue-green mountains across the lake on the horizon, calm gently rippled water extending across the entire foreground, and the large blue sky filled with natural billowing white clouds. Recover delicate roof tiles, wood grain, foliage, cloud shape, natural mountain texture, and tiny water ripples without crunchy artificial sharpening. Respect depth of field: cabin and distant scenery naturally clear, foreground plants deliberately soft. Keep natural restrained blue-green color and realistic daylight. The result should look like a high quality quiet natural photograph, not a painting, HDR, fantasy landscape, or illustration. Do not add text, a watermark, a person, another cabin, boats, or any new objects. No zoom, crop, compositional drift, oversaturation, dramatic recoloring, exaggerated contrast, or stylization. Retain all existing visible scene elements and their positions.
```

## 动效

`training-scenes.js` 仅移动天空、水面的局部纹理采样坐标。云层横移幅度上限约图宽 2.4%，周期约 84 秒；水面主要波纹周期约 5–7 秒，强度向近景增加。掩膜只向内羽化，同时检查采样点是否仍在相同区域，保护固定地形与植物。渲染缓存为 30 fps，低配设备约 24 fps；当前图片保持原生尺寸，超大图片才按 GPU 限制与像素预算降级。

暂停冻结当前画面；系统减少动态效果设置生效时停止动画。WebGL 不可用或上下文丢失时显示静态图片。动画与脑电分数、音乐音量独立，不改变图像亮度或整体镜头位置。
