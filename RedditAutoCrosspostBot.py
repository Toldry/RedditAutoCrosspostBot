import time
import traceback
import praw
from pprint import pprint
import re
import sys
#https://www.pythonforengineers.com/build-a-reddit-bot-part-1/

reddit = None
POST_SUFFIX_TEXT = '''
---

^^🤖 ^^this ^^comment ^^was ^^written ^^by ^^a ^^bot. ^^beep ^^boop ^^🤖

^^if ^^there's ^^a ^^problem, ^^please ^^report ^^it ^^to ^^/u/orqa'''

def main():
    print('running...')
    instantiate_reddit()

    #for submission in reddit.subreddit('learnpython').hot(limit=10):
    #    print(submission.title)

    scanned_subreddits = get_scanned_subreddits_string()

    subreddit_object = reddit.subreddit(scanned_subreddits)

    i = 0
    # infinite stream of comments from reddit
    print('listening to comment stream...')
    for comment in subreddit_object.stream.comments(skip_existing=True):
        print(i)
        i += 1
        handle_comment(comment)

def instantiate_reddit():
    username = 'AutoCrosspostBot'
    clientname = username
    #password = 'REDACTED'
    app_client_id = 'UwKgkrvtl9fpUw'
    #app_client_secret = 'REDACTED'
    version = '0.1'
    developername = 'orqa'
    useragent =  f'{clientname}/{version} by /u/{developername}'

    creds = read_credentials()
    password = creds[0]
    app_client_secret = creds[1]

    global reddit
    reddit = praw.Reddit(client_id=app_client_id,
                         client_secret=app_client_secret,
                         user_agent=useragent,
                         username=username,
                         password=password)
def read_credentials():
    lines = None
    with open('.credentials') as f:
        lines = [line.rstrip('\n') for line in f]
    return lines

def get_scanned_subreddits_string():
    list1 = ['announcements','funny','AskReddit','gaming','pics','science','aww','worldnews','movies','todayilearned','Music','videos','IAmA','news','gifs','EarthPorn','Showerthoughts','askscience','Jokes','blog','explainlikeimfive','books','food','LifeProTips','DIY','mildlyinteresting','Art','sports','space','gadgets','nottheonion','television','photoshopbattles','Documentaries','GetMotivated','listentothis','UpliftingNews','tifu','InternetIsBeautiful','history','Futurology','philosophy','OldSchoolCool','WritingPrompts','personalfinance','dataisbeautiful','nosleep','creepy','TwoXChromosomes','technology','AdviceAnimals','Fitness','memes','WTF','wholesomememes','politics','bestof','interestingasfuck','BlackPeopleTwitter','oddlysatisfying','leagueoflegends','travel','lifehacks','facepalm','dankmemes','pcmasterrace','me_irl','NatureIsFuckingLit','Tinder','nba','woahdude','PS4','AnimalsBeingBros','Whatcouldgowrong','AnimalsBeingJerks','relationships','tattoos','Overwatch','FoodPorn','reactiongifs','trippinthroughtime','atheism','BikiniBottomTwitter','Unexpected','gonewild','PewdiepieSubmissions','programming','gameofthrones','relationship_advice','boardgames','europe','malefashionadvice','Minecraft','gardening','pokemongo','instant_regret','photography','dadjokes','mildlyinfuriating','Game',]
    list2 = ['30ROCK','3Blue1Brown','3Dprinting','4chan','99percentinvisible','AAAAAAAAAAAAAAAAA','AccidentalAnime','AccidentalArtGallery','AccidentalCamouflage','AccidentalRenaissance','ActLikeYouBelong','AdobeIllustrator','AdPorn','ainbow','AliceIsntDead','alternativeart','ambigrams','AmItheAsshole','Amsterdam','Android','androidapps','ani_bm','AnimalsBeingDerps','AnimalSounds','announcements','ANormalDayInBrazil','ANormalDayInJapan','ANormalDayInRussia','Anticonsumption','antinatalism','antiwork','Anxiety','aphorisms','AquaticAsFuck','ArcherFX','architecture','arresteddevelopment','Art','ArtisanVideos','AsianPolitics','AskAnthropology','Askashittyparent','askdrugs','AskEurope','askgaybros','AskHistorians','AskReddit','asktransmen','ATBGE','atheism','australia','Austria','avocadosgonewild','aww','awwdlysatisfying','Awwducational','Ayahuasca','babyelephantgifs','badcode','badtaxidermy','badUIbattles','Bandersnatch','barkour','BatFacts','BattleRite','bayarea','beadsprites','beefanddairynetwork','Beekeeping','belgium','benchyarmsrace','berlin','bestof','bestof2018','BetterEveryLoop','beyondthegame','bicycling','bikecommuting','BikiniBottomTwitter','birdstakingthetrain','Bitcoin','blackmagicfuckery','blackmirror','BlackPeopleTwitter','blender','blog','bloodlinechampions','blursedimages','boardgames','bodyweightfitness','BOLIVIA','BollywoodRealism','Bombing','BonajMemeoj','books','Borderporn','Bossfight','BreadTube','britishproblems','brushybrushy','budapest','BurningMan','BuyItForLife','CalamariRaceTeam','California','Calligraphy','calmhands','canada','CasualConversation','CasualUK','casualworldnews','catastrophicsuccess','catsareliquid','cellular_automata','changemyview','chao','chaoticgood','chicago','chiptunes','Cinemagraphs','circlejerk','CitiesSkylines','CityPorn','classic4chan','ClassicalMemes','classicalmusic','classicalresources','Clickhole','Colombia','comfypasta','comics','community','confession','confusing_perspective','CongratsLikeImFive','ContraPoints','CoolCollections','copypasta','CozyPlaces','CPTSD','crafts','CrazyIdeas','Crittersoncapybaras','CryptoCurrency','css_irl','customhearthstone','Dance','dashcamgifs','data_irl','DataArt','dataisbeautiful','datascience','de','deepdream','DeepIntoYouTube','Deltarune','Denmark','Design','design_challenges','DesignPorn','DesirePaths','destabilized','different_sob_story','distance','diving','DMT','Documentaries','DoesAnybodyElse','DoesNotTranslate','dontyouknowwhoiam','DotA2','DotaVods','DrosteEffect','Drugs','drugscirclejerk','drums','Duhagrams','Dyslexia','EarthPorn','Effexor','ElectricScooters','electroswing','ElitistClassical','EngineeringStudents','Enhancement','Entomology','Escher','Esperanto','etchasketch','etymology','europe','eurovision','evilbuildings','Exhibit_Art','expats','explainlikeIAmA','explainlikeimfive','ExtraFabulousComics','fakealbumcovers','fakehistoryporn','FancyFollicles','fascinating','fashionporn','fashpics','fifthworldproblems','findareddit','FirstNameBasis','firstworldanswers','FitizenStories','flowers','foreskin_restoration','fractals','fragrance','france','FreeEBOOKS','FreezingFuckingCold','Frisson','frugaljerk','ftm','FTMfemininity','functionalprint','funny','FunnyandSad','futurama','Futurology','gamemusic','GamePhysics','Games','gaming','GamingDetails','Gary_The_Cat','gay_irl','gaybros','gaytransguys','GEB','generative','GeometryIsNeat','ggggbabybabybaby','ghibli','gifs','GlobalTalk','gonwild','graphic_design','GreenDawn','Hair','HairDye','happy','headphones','hearthstonecirclejerk','Heavymind','HeavySeas','hensinsync','HighQualityGifs','HistoryAnecdotes','HistoryPorn','HitBoxPorn','hmmm','HongKong','HotPaper','HouseOfCards','Howwastoday','Huel','HumansAreMetal','HumansBeingBros','hungary','HybridAnimals','IAmA','ich_iel','idiotproof','ik_ihe','IllegalLifeProTips','Illustration','ImaginarySliceOfLife','imsorryjon','Incense','INEEEEDIT','InfrastructurePorn','Infrastructurist','Instantbestfriends','Intactivists','interestingasfuck','InternetIsBeautiful','IRLEasterEggs','irlsmurfing','ironicsigns','Irony','Israel','italy','itrunsdoom','IWantOut','jackoffconfessions','javascript','Jewdank','jewelry','jewelrymaking','Jokes','juggling','Justrolledintotheshop','KeepOurNetFree','ketochow','KimmySchmidt','knitting','ktane','kurzgesagt','LaBeauteMasculine','Ladybonersgonecuddly','LangGeomMusicCode','LateStageCapitalism','lava','learnmath','LearnUselessTalents','Lettering','letters_ur_username','lgbt','lifehacks','LifeProTips','Lightbulb','likeus','linguistics','listentothis','loadingicon','logodesign','logophilia','lolgrindr','lolwat','london','LosAngeles','LSD','MadeMeSmile','malapropisms','malefashionadvice','MaleMidriff','MaliciousCompliance','ManufacturingPorn','MapPorn','mapporncirclejerk','marijuanaenthusiasts','MarineBiologyGifs','mastodon_social','math','mathpics','mathriddles','MDMA','mdmatherapy','mechanical_gifs','media_criticism','medicine','Meditation','meirl','MemeYourEnthusiasm','MensHighJinx','MensLib','microdosing','MicroPorn','microscopy','midburn','mildlyinteresting','mildlysatisfying','minimalism','minimalism_jerk','Miniworlds','MisleadingPuddles','mistakeproof','ModernFashionPorn','moi_dlvv','morbidlybeautiful','morbidquestions','motocamping','MotorcycleLogistics','motorcycles','MovieDetails','MoviePosterPorn','movies','MrRobot','msiladnav','MuseumOfReddit','Music','musictheory','mycology','NatureGifs','NatureIsFuckingLit','natureismetal','natureisterrible','neology','NetflixSexEducation','Netherlands','netsec','neutralnews','nevertellmetheodds','NewRiders','NewsPorn','newzealand','nightvale','NintendoSwitch','nocontextpics','nonononoyes','norge','NoSillySuffix','NoStupidQuestions','nottheonion','nsfwfunctionalprints','nyc','oakland','OddlyArousing','oddlysatisfying','oddlyterrifying','ofcoursethatsathing','oglaf','Okami','OlfactoryGifs','olympics','omad','Oman','onebag','oneliners','orcagifs','orcas','organ','OrganizationPorn','origami','OutOfTheLoop','Overwatch','Oxymoron','pAIperclip','Palestine','PandR','Pareidolia','PeopleFuckingDying','perfectloops','PerfectTiming','PERU','philosophy','photoshopbattles','piano','pics','piercing','place','Plumeria','podcasts','pointlesslygendered','polyamory','PornhubComments','Portal','Portland','Portmanteau','Posters','powerwashingporn','PraiseTheCameraMan','privacy','proceduralgeneration','ProgrammerHumor','ProgrammerTIL','programming','PropagandaPosters','ProperAnimalNames','PsychedSubstance','Psychonaut','PulitzerComments','quotes','RainbowEverything','raining','raisedbynarcissists','RandomKindness','RationalPsychonaut','reallifedoodles','Recursion','RedditAlternatives','RedditLaqueristas','redneckengineering','relationships','Repaintings','replications','ReverseEngineering','RoastMe','RogerRabbitEffect','RussianDoll','SampleSize','sanfrancisco','saplings','sbubby','Scarfolk','science','scriptedasiangifs','scuba','secondsketch','SexToys','shitduolingosays','shittyama','shittyaskalawyer','shittyaskscience','ShittyDebates','ShittyLifeProTips','shittylinguistics','shittyprogramming','ShittyQuotesPorn','shittyrobots','shortscarystories','Showerthoughts','shroomers','ShroomID','shrooms','SiliconValleyHBO','Simulated','sixwordstories','skylineporn','Slovenia','slowcooking','SlyGifs','smashbros','SmashBrosUltimate','SMBCComics','SnakesWithHats','SocialEngineering','solipsism','solotravel','sothatswhatitsfor','SoundsLikeMusic','southpark','soylent','SpaceBased','spain','specializedtools','sportsarefun','StallmanWasRight','starcraft','StartledCats','sticker','stickers','Stims','stocks','StockVideoStories','StoppedWorking','StreetArtPorn','StreetEpistemology','stringart','subnautica','subredditoftheday','SubredditSimMeta','SubredditSimulator','Suomi','SurgeryGifs','sustainability','SWARJE','sweden','Switzerland','sydney','SympatheticMonsters','TalesFromRetail','tattoos','technology','technologyconnections','techsupportmacgyver','Telegram','TelegramChannels','television','Terraria','tf2','thalassophobia','ThanksObama','theinternetofshit','TheLastAirbender','thenetherlands','theocho','TheOnion','theonionwasright','thetruthpodcast','theydidthemath','ThingsCutInHalfPorn','ThirdSentenceHappy','thisweekinreview','THUNKShow','TIHI','tilwtf','Tinder','tinycode','TinyHouses','tipofmytongue','Tiresaretheenemy','titleporn','todayilearned','tombstoning','tooktoomuch','toptalent','torrents','transgendercirclejerk','travel','trees','trendingsubreddits','Trichsters','TripCaves','TrollCoping','TrollXChromosomes','tumblr','TumblrAtRest','TwoSentenceHorror','typography','Undertale','UnethicalLifeProTips','union','unitedkingdom','unknownvideos','unstirredpaint','untitledgoosegame','upvotebecauseboy','UrbanHell','Urbanism','urbanplanning','vandwellers','vaporents','VaporwaveAesthetics','vegangifrecipes','veganrecipes','vexillology','vexillologycirclejerk','veximation','videos','vine','visualization','VoiceActing','WatchandLearn','watchpeoplesurvive','WeatherGifs','web_design','webcomicname','webdev','Wellthatsucks','Wellworn','westworld','whatisthisthing','whatsthisplant','whitepeoplegifs','WhitePeopleTwitter','Wholesome4chan','wholesomememes','wholesomevandalism','wikipedia','wimmelbilder','wlw_irl','woahdude','worldnews','wow','WritingPrompts','wtfstockphotos','Xiaomi','xkcd','XRayPorn','yarnbombing','yo_elvr','youdontsurf','youseeingthisshit','youtubehaiku','YUROP','zappafied','ZeroWaste','Zoomies',]
    list3 = ['test','test9']

    blacklist = ['hmmm']
    
    scanned_subreddits_list = list2[0:500] #575 588

    return '+'.join(scanned_subreddits_list)

def get_existing_crosspost(comment, other_subreddit):
    try:
        submissions = [s for s in reddit.subreddit(other_subreddit).search(query = comment.link_url, sort='new', time_filter='all')]
    except Exception as e:
        if e.args[0] == 'Redirect to /submit': #when reddit tries redirecting a search query of a link to the submission page, that means 0 results were found for the search query
            return None
        elif e.args[0] == 'Redirect to /subreddits/search': #when reddit redirects to /subreddits/search that means the subreddit `other_subreddit` doesn't exist
            return None #TODO write this better
        elif e.args[0] == 'received 403 HTTP response': #this error is recieved when the other_subreddit is private "You must be invited to visit this community"
            return None
        else:
            raise

    if len(submissions) == 0:
        return None
    return submissions[-1] #return oldest submission that matches search

def handle_existing_post(comment, existing_post):
    text = f'I found [this preexisting post]({existing_post.link_permalink}) in /{existing_post.subreddit_name_prefixed} with the same link as the original post.'
    text += POST_SUFFIX_TEXT
    return comment.reply(text)

moderators = {}
def get_all_moderators():
    global moderators
    if bool(moderators): #if the moderators dict is not empty
        return moderators

    scanned_subreddits = get_scanned_subreddits_string()
    print('retrieving all moderators...')
    subreddits = scanned_subreddits.split('+')
    for sub in subreddits:
        print(f'getting moderators of /r/{sub}')
        moderators[sub] = reddit.subreddit(sub).moderator()
    print('finished retrieving all moderators:')
    #pprint(moderators)
    return moderators

link_id_regex = re.compile(r'^(.+)_(.+)$')
ratelimit_error_regex = re.compile(r'you are doing that too much\. try again in (\d)+ minutes\.')
def handle_comment(comment):
    global link_id_regex
    global ratelimit_error_regex
    
    if is_mod_post(comment):
        print('this is a moderator post. skipping.')
        return

    if not is_top_level_comment(comment):
        print('this is not a top-level comment. skipping.')
        return
    
    other_subreddit = check_pattern(comment)
    if not other_subreddit:
        print('no pattern match. skipping.')
        return
    else:
        print('match found')


    existing_post = get_existing_crosspost(comment, other_subreddit)
    if existing_post:
       print('existing crosspost found') 
       reply = handle_existing_post(comment, existing_post)
       print(f'replied to comment, link: {reply.link_permalink}')
       return

    
    cleaned_link_id = link_id_regex.search(comment.link_id).groups()[1]
    submission = reddit.submission(id=cleaned_link_id)

    try:
        cross_post = submission.crosspost(subreddit=other_subreddit, send_replies=False)
        print(f'crosspost succesful. link to post: www.reddit.com{cross_post.permalink}')
        reply_to_crosspost_suggestion_comment(comment, cross_post, other_subreddit)
        time.sleep(2)
    except Exception as e:
        print(f'crosspost failed: {str(e)}')
        if len(e.args) == 0:
            return
        search_ratelimit_error_message = cleaned_link_id = ratelimit_error_regex.search(e.args[0])
        if search_ratelimit_error_message is not None:
            minutes_to_wait = int(search_ratelimit_error_message.groups()[0])
            print(f'waiting {minutes_to_wait} minutes...')
            time.sleep(minutes_to_wait * 60)
        pass

def is_mod_post(comment):
    moderators = get_all_moderators()
    return comment.author in moderators[comment.subreddit.display_name]  #TODO check if this is the correct attribute

subreddit_regex = re.compile(r'^(\/)?r\/([a-zA-Z0-9-_]+)$')
def check_pattern(comment):
    global subreddit_regex

    searchResult = subreddit_regex.search(comment.body)
    if searchResult is None:
        return None

    groups = searchResult.groups()

    other_subreddit = groups[1]
    print(f'other_subreddit={other_subreddit}')
    return other_subreddit

def is_top_level_comment(comment):
    return comment.parent_id == comment.link_id

def reply_to_crosspost_suggestion_comment(comment, cross_post, other_subreddit):
    text = f'I crossposted to /r/{other_subreddit}:'
    text += '\n\n'
    text += 'www.reddit.com' + cross_post.permalink
    text += POST_SUFFIX_TEXT
    return comment.reply(text)

if __name__ == '__main__':
    # execute only if run as a script
    try:
        main()
    except Exception as e:
        print(f'FATAL ERROR: {str(e)}')
        print('traceback: ')
        print(traceback.print_tb(e.__traceback__))
        raise