# Translate Vietnam

Ứng dụng Windows dịch văn bản trong clipboard, chạy nền ở khay hệ thống. Bôi đen văn bản và nhấn nhanh `Ctrl+C` hai lần: ứng dụng tự nhận diện ngôn ngữ và thay nội dung clipboard bằng bản dịch, bạn chỉ việc `Ctrl+V`. Copy một lần vẫn là copy bình thường.

## Tính năng

- Chạy nền ở khay hệ thống, có Bảng điều khiển gọn (nút **Mở rộng** để hiện thêm tùy chọn).
- **Dịch khi nhấn `Ctrl+C` hai lần** (mặc định), hoặc dịch mọi lần copy (đổi ở ô *Dịch khi*).
- Chế độ **Tự động hai chiều**: tiếng Việt → tiếng Anh, tiếng Anh và ngôn ngữ khác → tiếng Việt.
- Chế độ cố định **Sang tiếng Việt** / **Sang tiếng Anh**, và **Tắt** để copy bình thường.
- **Dịch theo lĩnh vực bằng AI Gemini** (tùy chọn): nhập API key Gemini và chọn lĩnh vực (mặc định *Kỹ thuật*) để bản dịch dùng đúng thuật ngữ chuyên ngành. Không có key thì dùng Google Dịch.
- Tự chuyển dịch vụ: Gemini hết lượt miễn phí hoặc lỗi → Google Dịch → MyMemory.
- **Bảo vệ dữ liệu**: không gửi đi dịch nội dung từ trình quản lý mật khẩu, chuỗi giống mật khẩu / mã token / URL / đường dẫn / email, và nội dung copy từ các app trong danh sách loại trừ.
- Icon khay hiện chế độ (`A` / `V` / `E` / `–`) và tiến độ dịch tô màu từ trên xuống; hiện 100% khi bản dịch đã vào clipboard.
- Phím tắt toàn cục, đổi được trong Bảng điều khiển.
- Tự chạy cùng Windows (bật/tắt trong Bảng điều khiển), không cần quyền Administrator.

## Cách dùng bản EXE

1. Tải `TranslateVietnam.exe` trong phần [Releases](https://github.com/tranvankha1989/Translate_EV/releases/latest).
2. Chạy file. Cửa sổ **Cài đặt** hiện ra:
   - Chọn ổ đĩa / thư mục cài đặt (mặc định `C:\Program Files\Translate Vietnam`). Cài vào Program Files thì Windows sẽ hỏi quyền quản trị khi cài và mỗi lần cập nhật; chọn thư mục khác (ví dụ `D:\Apps`) thì không hỏi.
   - Tích / bỏ tích *Tạo lối tắt ngoài Desktop*, rồi bấm **Cài đặt**.
   - Hoặc **Chạy không cài đặt** để dùng file exe trực tiếp (không hỏi lại; muốn cài sau thì vào Mở rộng → Khác → *Cài vào máy…*).
3. Ứng dụng mở ra từ thư mục đã cài, Bảng điều khiển hiện lên và chế độ Tự động đã bật sẵn. File vừa tải về có thể xóa.
4. Bôi đen văn bản, nhấn nhanh `Ctrl+C` hai lần, chờ icon ở khay hệ thống (cạnh đồng hồ) tô xanh đủ 100%, rồi `Ctrl+V`.

**Mở lại sau khi thoát:** bấm phím Windows, gõ *Translate* (lối tắt trong Start Menu), hoặc bấm lối tắt **Translate Vietnam** ngoài Desktop. Khi ứng dụng đang chạy, mở lại nó sẽ hiện Bảng điều khiển.

**Gỡ cài đặt:** Cài đặt Windows → Ứng dụng → *Translate Vietnam* → Gỡ cài đặt. Ứng dụng hỏi có xóa luôn cài đặt (API key…) hay giữ lại cho lần cài sau.

Cài không hỏi (cho quản trị viên): `TranslateVietnam.exe --install-to "D:\Apps" --no-desktop`.

## Điều khiển

| Thao tác | Tác dụng |
| --- | --- |
| Bấm trái vào icon | Tắt dịch / bật lại chế độ trước đó |
| Chuột phải vào icon | Chọn chế độ, mở Bảng điều khiển, Hướng dẫn, Thoát |
| `F8` | Chuyển sang chế độ Sang tiếng Việt và dịch luôn clipboard |
| `F9` | Chuyển sang chế độ Sang tiếng Anh và dịch luôn clipboard |
| `F10` | Tắt dịch / bật lại chế độ trước đó |

Các phím tắt đổi được trong Bảng điều khiển → Mở rộng → Phím tắt (bấm vào ô rồi nhấn phím mới; Backspace để bỏ phím). Nếu phím bị phần mềm khác chiếm, ứng dụng sẽ báo để bạn đổi phím khác.

## Dịch theo lĩnh vực bằng Gemini

1. Lấy API key miễn phí tại [Google AI Studio](https://aistudio.google.com/apikey).
2. Mở Bảng điều khiển → **Mở rộng** → tab **AI Gemini**, dán API key, bấm **Kiểm tra key**.
3. Chọn **Lĩnh vực** ở phần Điều khiển (có thể tự gõ lĩnh vực khác).

Model mặc định là **Tự động (Flash-Lite mới nhất)**: app gọi `gemini-flash-lite-latest`, tên rút gọn Google luôn trỏ tới bản Flash-Lite mới nhất. Nhờ vậy app không bị hỏng khi Google ngừng một model cũ. Nếu Google bỏ tên rút gọn này, app tự dùng `gemini-3.5-flash-lite`. Có thể chọn hoặc gõ model cụ thể khác trong ô Model.

Lưu ý:

- Gói miễn phí của Gemini có giới hạn số lượt mỗi phút và mỗi ngày (xem trong Google AI Studio), tính theo project chứ không theo API key, làm mới lúc nửa đêm giờ Thái Bình Dương. Khi hết lượt, ứng dụng tự dùng Google Dịch và thử lại Gemini sau.
- Cách tiết kiệm lượt có sẵn: chỉ nhấn `Ctrl+C` hai lần mới gọi Gemini; copy lại đoạn đã dịch trong lúc app chạy không tốn lượt; đoạn dài được gửi trong một lần gọi. Số lượt đã dùng hôm nay hiện ở phần Điều khiển (chỉ lưu con số, không lưu nội dung).
- Ở gói miễn phí, Google có thể dùng nội dung gửi lên để cải thiện sản phẩm. Dùng gói trả phí nếu văn bản của bạn cần bảo mật.
- API key được lưu đã mã hóa (Windows DPAPI) trong file cài đặt, chỉ tài khoản Windows của bạn giải mã được.

## Quyền riêng tư

Khi đang bật dịch, văn bản bạn copy được gửi tới dịch vụ dịch trực tuyến (Gemini nếu có API key, Google Dịch, hoặc MyMemory khi Google bận). Ứng dụng không lưu nội dung bạn copy. Có thể chỉnh các bộ lọc trong Mở rộng → **Bảo vệ dữ liệu**.

## Cập nhật

Phiên bản đang dùng hiện ở dòng trên cùng của Bảng điều khiển (**Phiên bản x.y.z**). Ứng dụng tự kiểm tra bản mới trên [GitHub Releases](https://github.com/tranvankha1989/Translate_EV/releases) mỗi ngày; muốn kiểm tra ngay thì **bấm vào dòng phiên bản**:

- Đang là bản mới nhất: hiện thông báo *Bạn đang dùng bản mới nhất*.
- Có bản mới: dòng đó **nhấp nháy đỏ "↑ Cập nhật lên x.y.z"**. Bấm vào sẽ hiện các tính năng mới và hỏi có muốn nâng cấp không; bấm **Đồng ý** là ứng dụng tự tải, kiểm tra mã SHA-256, thay file exe và khởi động lại, tiến độ hiện ngay tại dòng phiên bản (hoặc **Để sau**).

Không cần cài git hay công cụ nào khác.

**Không cần gỡ cài đặt, cài đặt không bị mất.** Toàn bộ cài đặt — chế độ, lĩnh vực, phím tắt, API key, danh sách loại trừ — được lưu riêng trong `%APPDATA%\TranslateVietnam\config.json`, không nằm trong file exe, nên cập nhật (tự động hay thủ công) vẫn giữ nguyên mọi thứ. Chạy một file exe mới hơn bản đã cài cũng tự nâng cấp bản đã cài, không hỏi lại thư mục.

### Đang dùng bản cũ chưa có tính năng tự cập nhật

Bản cũ không tự biết có bản mới, nên cần thay file **một lần**:

1. Chuột phải vào icon ở khay hệ thống → **Thoát**.
2. Tải `TranslateVietnam.exe` mới trong [Releases](https://github.com/tranvankha1989/Translate_EV/releases/latest).
3. Chạy file mới và cài như hướng dẫn ở trên (bản cài sẽ tự nhận chế độ chạy cùng Windows). Sau đó có thể xóa file exe cũ.
4. Chạy `TranslateVietnam.exe`. Từ đây trở đi ứng dụng tự cập nhật.

Lưu ý từ bản 1.0.0: mặc định cần **nhấn nhanh `Ctrl+C` hai lần** mới dịch. Muốn dịch mỗi lần copy như bản cũ, chọn *Dịch khi: Mỗi lần copy* trong Bảng điều khiển.

## Cài đặt và nhật ký lỗi

- Cài đặt: `%APPDATA%\TranslateVietnam\config.json`.
- Nhật ký lỗi: `app.log` cạnh `TranslateVietnam.exe` (hoặc cạnh `app.py` khi chạy từ mã nguồn). Nếu thư mục đó không ghi được, log nằm tại `%LOCALAPPDATA%\TranslateVietnam\app.log`. File log tối đa 5 MB; khi đầy, ứng dụng tự xóa phần cũ nhất và giữ lại khoảng 2,5 MB log mới nhất.

Nếu không thấy icon, kiểm tra khu vực icon ẩn của Windows. Nếu dịch không hoạt động, kiểm tra kết nối Internet và `app.log`.

## Chạy từ mã nguồn

Yêu cầu Windows và Python 3.11 trở lên:

```bat
py -3.11 -m pip install -r requirements.txt
run.bat
```

Chạy test:

```bat
py -3.11 -m unittest test_features test_translator test_app_log test_updater test_installer
```

## Tự build EXE

```bat
build_exe.bat
```

Script cài thư viện, chạy test, tạo icon rồi build `dist\TranslateVietnam.exe`. Bản build là file độc lập, máy người dùng không cần cài Python.

## Phát hành bản mới (dành cho nhà phát triển)

Cần Python 3.11, git và [GitHub CLI](https://cli.github.com) đã đăng nhập (`gh auth login`) — chỉ trên máy phát hành, người dùng không cần.

1. Tăng `APP_VERSION` trong `config.py` (ví dụ `1.0.0` → `1.0.1`).
2. (Tùy chọn) Ghi nội dung thay đổi vào `release_notes.md`; nếu không có, GitHub tự tạo ghi chú từ các commit. Nên có mục `## Có gì mới` (gạch đầu dòng `- ...`): cửa sổ cập nhật trong app hiển thị đúng mục này cho người dùng.
3. Commit thay đổi, rồi chạy `release.bat`.

Script đẩy code lên GitHub, chạy test, build exe, tạo file `TranslateVietnam.exe.sha256` và tạo bản phát hành `v<phiên bản>` kèm hai file đó. Ứng dụng của người dùng sẽ tự báo có bản mới trong vòng một ngày.

## Giấy phép

Bạn nên thêm giấy phép mã nguồn phù hợp trước khi công khai repository, ví dụ MIT nếu muốn cho phép cộng đồng tự do sử dụng và chỉnh sửa.
